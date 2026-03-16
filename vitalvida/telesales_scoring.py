"""
M24 — Telesales Performance Scoring & Agent Blocking
telesales_scoring.py

run_nightly_telesales_scoring() runs at midnight via cron: 0 0 * * *
Creates Telesales Performance Record for every active closer.
Blocks low performers based on Commission Tier from M23.

Also provides:
  override_block() — manager manually unblocks a rep with reason
  expire_bonus_approvals() — hourly check for expired bonus requests
"""

import frappe
from frappe.utils import (
    now_datetime, today, get_first_day_of_week, add_days, add_to_date
)


def run_nightly_telesales_scoring() -> None:
    """
    Runs at midnight via cron: 0 0 * * *
    Computes performance record for every active telesales closer.
    """
    from vitalvida.dsr import compute_telesales_dsr
    from vitalvida.vitalvida.doctype.vv_commission_settings.vv_commission_settings import (
        get_commission_settings, match_tier
    )

    week_start = str(get_first_day_of_week(today()))
    week_end = str(add_days(week_start, 6))
    now = now_datetime()

    try:
        settings = get_commission_settings()
    except Exception as e:
        frappe.log_error(
            f"M24: Cannot run scoring — commission settings error: {str(e)}",
            "M24 Settings Error"
        )
        return

    active_closers = frappe.get_all(
        "Telesales Closer",
        filters={"is_active": 1},
        fields=["name", "closer_name"]
    )

    scored = 0
    blocked = 0
    errors = 0

    for closer in active_closers:
        try:
            dsr = compute_telesales_dsr(closer.name, week_start, week_end)
            tier = match_tier(dsr["dsr_strict"], settings)

            # Count reassigned orders
            reassigned = frappe.db.sql("""
                SELECT COUNT(*) as cnt FROM `tabVV Order`
                WHERE telesales_rep = %s
                AND delivery_agent IS NOT NULL
                AND creation BETWEEN %s AND %s
                AND order_status IN ('Cancelled', 'Returned')
            """, (closer.name, week_start, week_end), as_dict=True)
            total_reassigned = int(reassigned[0].cnt) if reassigned else 0

            # Count attended within 15 minutes
            attended_15 = frappe.db.sql("""
                SELECT COUNT(*) as cnt
                FROM `tabTelesales Assignment Log` tal
                INNER JOIN `tabVV Order` o ON o.name = tal.`order`
                WHERE tal.closer = %s
                AND tal.assigned_at BETWEEN %s AND %s
                AND o.status_changed_at IS NOT NULL
                AND TIMESTAMPDIFF(MINUTE, tal.assigned_at, o.status_changed_at) <= 15
                AND o.order_status IN ('Confirmed', 'Assigned', 'Out for Delivery',
                                        'Delivered', 'Paid')
            """, (closer.name, week_start, week_end), as_dict=True)
            attended_count = int(attended_15[0].cnt) if attended_15 else 0

            # Upsert Performance Record
            existing = frappe.db.exists("Telesales Performance Record", {
                "telesales_rep": closer.name,
                "period_start": week_start,
            })

            record_data = {
                "telesales_rep": closer.name,
                "period_start": week_start,
                "period_end": week_end,
                "total_assigned": dsr["assigned"],
                "total_delivered": dsr["paid"],
                "total_ghosted": dsr["ghosted"],
                "total_reassigned": total_reassigned,
                "attended_in_15min": attended_count,
                "delivery_rate": dsr["dsr_strict"],
                "ghost_rate": dsr["ghost_rate"],
                "performance_tier": tier["tier_name"],
                "bonus_multiplier": tier["bonus_multiplier"],
                "bonus_flag": tier["tier_name"].upper(),
                "assignments_blocked": 1 if tier["blocks_new_assignments"] else 0,
                "computed_at": now,
            }

            if existing:
                frappe.db.set_value(
                    "Telesales Performance Record", existing, record_data
                )
            else:
                doc = frappe.get_doc({
                    "doctype": "Telesales Performance Record",
                    **record_data
                })
                doc.insert(ignore_permissions=True)

            # Update closer's is_blocked flag
            if tier["blocks_new_assignments"]:
                frappe.db.set_value("Telesales Closer", closer.name, "is_blocked", 1)
                blocked += 1
            else:
                frappe.db.set_value("Telesales Closer", closer.name, "is_blocked", 0)

            scored += 1

        except Exception as e:
            frappe.log_error(
                f"M24: Scoring failed for closer={closer.name}: {str(e)}",
                "M24 Scoring Error"
            )
            errors += 1

    frappe.db.commit()

    frappe.log_error(
        f"M24: Nightly scoring — scored={scored}, blocked={blocked}, "
        f"errors={errors}, period={week_start} to {week_end}",
        "M24 Scoring Summary"
    )


def override_block(telesales_rep: str, reason: str) -> None:
    """
    Manager manually overrides a block on a telesales rep.
    Creates immutable Block Override Log entry.
    """
    if not reason or not reason.strip():
        frappe.throw("A reason is required to override a block.")

    frappe.db.set_value("Telesales Closer", telesales_rep, "is_blocked", 0)

    frappe.get_doc({
        "doctype": "Block Override Log",
        "telesales_rep": telesales_rep,
        "override_reason": reason,
    }).insert(ignore_permissions=True)

    frappe.db.commit()


def expire_bonus_approvals() -> None:
    """
    Runs hourly via cron: 0 * * * *
    Auto-rejects expired Bonus Approval Requests.
    """
    now = now_datetime()

    expired = frappe.db.sql("""
        SELECT name, employee
        FROM `tabBonus Approval Request`
        WHERE status = 'Pending'
        AND expires_at IS NOT NULL
        AND expires_at < %s
    """, (now,), as_dict=True)

    for req in expired:
        try:
            frappe.db.set_value("Bonus Approval Request", req.name, {
                "status": "Expired",
                "rejection_reason": "Auto-rejected: approval window expired.",
            })
        except Exception as e:
            frappe.log_error(
                f"M24: Bonus expiry failed for {req.name}: {str(e)}",
                "M24 Bonus Expiry Error"
            )

    if expired:
        frappe.db.commit()


def calculate_bonus(employee: str, employee_type: str, bonus_amount: float,
                    dry_run: bool = False) -> dict:
    """
    M23 addendum: Calculate bonus with approval tier routing.
    If dry_run=True, returns result without creating any records.
    """
    from vitalvida.vitalvida.doctype.vv_commission_settings.vv_commission_settings import (
        get_commission_settings
    )

    settings = get_commission_settings()
    fc_threshold = float(getattr(settings, "bonus_approval_fc_threshold", None) or 15000)
    gm_threshold = float(getattr(settings, "bonus_approval_gm_threshold", None) or 50000)
    ceo_threshold = float(getattr(settings, "bonus_approval_ceo_threshold", None) or 100000)
    expiry_days = int(getattr(settings, "bonus_approval_expiry_days", None) or 7)

    result = {
        "employee": employee,
        "bonus_amount": bonus_amount,
        "requires_approval": False,
        "approver_role": None,
        "auto_approved": False,
    }

    if bonus_amount >= ceo_threshold:
        result["requires_approval"] = True
        result["approver_role"] = "CEO"
    elif bonus_amount >= gm_threshold:
        result["requires_approval"] = True
        result["approver_role"] = "GM"
    elif bonus_amount >= fc_threshold:
        result["requires_approval"] = True
        result["approver_role"] = "Finance Controller"
    else:
        result["auto_approved"] = True

    if dry_run:
        return result

    if result["auto_approved"]:
        # No approval needed
        return result

    # Create approval request
    expires_at = add_to_date(now_datetime(), days=expiry_days)
    frappe.get_doc({
        "doctype": "Bonus Approval Request",
        "employee": employee,
        "employee_type": employee_type,
        "bonus_amount": bonus_amount,
        "required_approver_role": result["approver_role"],
        "status": "Pending",
        "expires_at": expires_at,
        "urgency": _compute_urgency(expires_at),
    }).insert(ignore_permissions=True)
    frappe.db.commit()

    return result


def _compute_urgency(expires_at) -> str:
    """Compute urgency flag based on time remaining."""
    from frappe.utils import time_diff_in_hours
    hours_left = time_diff_in_hours(expires_at, now_datetime())
    if hours_left <= 0:
        return "expired"
    elif hours_left <= 24:
        return "urgent"
    elif hours_left <= 72:
        return "high"
    return "normal"
