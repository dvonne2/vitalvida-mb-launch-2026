"""
M15 — Friday Stock Count Reminder & Escalation Scheduler
stock_count_reminder.py

Two scheduled functions:
  send_friday_reminders()   — cron: 0 11 * * 5  (11:00 AM every Friday)
  escalate_missing_counts() — cron: 30 12 * * 5 (12:30 PM every Friday)
"""

import frappe
from frappe.utils import now_datetime, get_datetime, today, get_first_day_of_week


def send_friday_reminders() -> None:
    """
    Runs at 11:00 AM every Friday.
    Sends WhatsApp reminder to all active DAs who have not yet
    submitted a Confirmed Stock Count for this Friday.
    """
    from vitalvida.notifications import send_notification

    this_friday = _get_this_friday()

    active_das = frappe.get_all("Delivery Agent", filters={"active": 1},
                                fields=["name", "agent_name", "phone"])

    sent = 0
    errors = 0

    for da in active_das:
        try:
            already_confirmed = _has_confirmed_count_this_week(da.name, this_friday)
            if already_confirmed:
                continue

            stub = frappe._dict({
                "name": f"friday-reminder-{da.name}",
                "customer_name": da.agent_name,
                "customer_phone": da.phone,
                "delivery_agent_name": da.agent_name,
                "system_url": frappe.utils.get_url(),
                "total_payable": 0,
                "package_contents": "",
                "address": "",
            })

            send_notification(
                stub,
                event="FridayStockCountReminder",
                recipient_type="Delivery Agent",
                sender_channel="DA"
            )
            sent += 1

        except Exception as e:
            frappe.log_error(
                f"M15: Friday reminder failed for DA={da.name}: {str(e)}",
                "M15 Reminder Error"
            )
            errors += 1

    frappe.log_error(
        f"M15: Friday reminders — sent={sent}, errors={errors}, friday={this_friday}",
        "M15 Reminder Summary"
    )


def escalate_missing_counts() -> None:
    """
    Runs at 12:30 PM every Friday.
    For each active DA with no Confirmed count for this Friday:
      1. Freeze all their DA Warehouses
      2. Create a Critical Stock Variance record
      3. Add 1 strike (Photo Non-Compliance)
      4. Alert Operations
    DAs who submitted on time are NOT touched.
    """
    from vitalvida.freeze import freeze_da_warehouse
    from vitalvida.notifications import send_notification

    this_friday = _get_this_friday()

    active_das = frappe.get_all("Delivery Agent", filters={"active": 1},
                                fields=["name", "agent_name", "phone"])

    frozen_count = 0
    skipped_count = 0
    errors = 0

    for da in active_das:
        try:
            already_confirmed = _has_confirmed_count_this_week(da.name, this_friday)
            if already_confirmed:
                skipped_count += 1
                continue

            # 1. Freeze all warehouses for this DA
            warehouses = frappe.get_all("DA Warehouse",
                                        filters={"delivery_agent": da.name},
                                        fields=["name", "product"])
            for wh in warehouses:
                freeze_da_warehouse(
                    da.name,
                    wh.product,
                    reason="Friday stock count not submitted by 12:00 noon deadline"
                )

            # 2. Add strike
            _add_strike(
                delivery_agent=da.name,
                source="Photo Non-Compliance",
                reason=f"No Friday stock count submitted by 12:00 noon on {this_friday}"
            )

            # 2b. Loop 2.5: create a DA Restock Block (idempotent - one active block
            # per DA). Independent of the warehouse freeze above: the freeze is an
            # operational hold; the restock block means "no new stock until the DA
            # reconciles". Enforced at dispatch via can_hold_custody.
            try:
                _ensure_restock_block(
                    da.name,
                    reason=f"No Friday stock count submitted by 12:00 noon on {this_friday}"
                )
            except Exception as _rb_err:
                frappe.log_error(
                    f"M15: Restock block create failed for DA={da.name}: {_rb_err}",
                    "M15 Restock Block Error"
                )

            # 3. Create Critical Stock Variance
            _create_missing_count_variance(da.name, this_friday)

            # 4. Alert Operations
            try:
                stub = frappe._dict({
                    "name": f"missed-count-{da.name}-{this_friday}",
                    "customer_name": da.agent_name,
                    "customer_phone": "",
                    "delivery_agent_name": da.agent_name,
                    "total_payable": 0,
                    "package_contents": "",
                    "address": "",
                })
                send_notification(
                    stub,
                    event="StockCountMissed",
                    recipient_type="Owner",
                    sender_channel="Transactional"
                )
            except Exception:
                pass  # Alert failure must not block freeze

            frozen_count += 1

        except Exception as e:
            frappe.log_error(
                f"M15: Escalation failed for DA={da.name}: {str(e)}",
                "M15 Escalation Error"
            )
            errors += 1

    frappe.log_error(
        f"M15: Friday escalation — frozen={frozen_count}, "
        f"skipped={skipped_count}, errors={errors}, friday={this_friday}",
        "M15 Escalation Summary"
    )


# ─── Internal Helpers ─────────────────────────────────────────────────────────

def _get_this_friday() -> str:
    """Returns today's date string (the scheduler only runs on Fridays via cron)."""
    return today()


def _has_confirmed_count_this_week(delivery_agent: str, this_friday: str) -> bool:
    """
    Returns True if DA has at least one Stock Count with count_status=Confirmed
    where count_date >= this_friday. Scoped to this Friday only — not last week.
    """
    confirmed = frappe.db.exists("Stock Count", {
        "delivery_agent": delivery_agent,
        "count_status": "Confirmed",
        "count_date": [">=", this_friday]
    })
    return bool(confirmed)


def _add_strike(delivery_agent: str, source: str, reason: str) -> None:
    """Insert a DA Strike Log entry and recompute DA strike_count."""
    try:
        doc = frappe.get_doc({
            "doctype": "DA Strike Log",
            "delivery_agent": delivery_agent,
            "source": source,
            "reason": reason,
            "created_by": "Administrator",
            "created_at": now_datetime(),
            "is_cleared": 0,
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        _recompute_strike_count(delivery_agent)
    except Exception as e:
        frappe.log_error(
            f"M15: Failed to add strike for DA={delivery_agent}: {str(e)}",
            "M15 Strike Error"
        )


def _recompute_strike_count(delivery_agent: str) -> None:
    """Recompute active strike count and auto-suspend at 3."""
    active_strikes = frappe.db.count("DA Strike Log", {
        "delivery_agent": delivery_agent,
        "is_cleared": 0
    })
    update = {"strike_count": active_strikes}
    if active_strikes >= 3:
        update["strike_status"] = "Suspended"
        update["active"] = 0
        _reassign_open_orders(delivery_agent)
    else:
        update["strike_status"] = "Active"
    frappe.db.set_value("Delivery Agent", delivery_agent, update)
    frappe.db.commit()


def _reassign_open_orders(delivery_agent: str) -> None:
    """Reassign all open orders from suspended DA using existing M10 round-robin."""
    try:
        open_orders = frappe.get_all("VV Order", filters={
            "delivery_agent": delivery_agent,
            "order_status": ["in", ["Assigned", "Out for Delivery"]]
        }, fields=["name"])

        for order in open_orders:
            frappe.db.set_value("VV Order", order.name, {
                "delivery_agent": None,
                "order_status": "Pending"
            })
        if open_orders:
            frappe.db.commit()
    except Exception as e:
        frappe.log_error(
            f"M15: Failed to reassign orders for suspended DA={delivery_agent}: {str(e)}",
            "M15 Reassign Error"
        )


def _create_missing_count_variance(delivery_agent: str, count_date: str) -> None:
    """Create a Critical Stock Variance record for a missed Friday count."""
    try:
        doc = frappe.get_doc({
            "doctype": "Stock Variance",
            "delivery_agent": delivery_agent,
            "product": "N/A",
            "system_stock": 0,
            "counted_stock": 0,
            "variance": 0,
            "variance_percent": 100.0,
            "variance_status": "Critical",
            "checked_at": now_datetime(),
            "notes": f"No stock count submitted by Friday noon on {count_date}",
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(
            f"M15: Failed to create missing count variance for DA={delivery_agent}: {str(e)}",
            "M15 Variance Error"
        )


def _ensure_restock_block(delivery_agent: str, reason: str) -> None:
    """
    Loop 2.5 - idempotently create an active DA Restock Block for a DA.
    If an active block already exists for this DA, do nothing (Law 22).
    The DA Restock Block controller's before_insert sets blocked_by/blocked_at/
    is_active; after_insert zeroes reorder points. Enforced at dispatch via
    can_hold_custody.
    """
    existing = frappe.db.exists("DA Restock Block", {"delivery_agent": delivery_agent, "is_active": 1})
    if existing:
        return
    frappe.get_doc({
        "doctype": "DA Restock Block",
        "delivery_agent": delivery_agent,
        "reason": reason,
    }).insert(ignore_permissions=True)
    frappe.db.commit()
