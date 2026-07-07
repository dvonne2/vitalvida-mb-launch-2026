"""
Champion earning core (v5.1).

Turns an *earned* achievement into a Bonus Event. We reuse the EXISTING routing
thresholds and the EXISTING `Bonus Approval Request` doctype — no parallel money
system. But we do NOT rely on calculate_bonus() to *persist* the record, because
its real path drops sub-threshold bonuses (it returns auto_approved without
creating a request). Champion bonuses (₦1,000 upsell, ₦5,000 DPSR) are below the
FC threshold, so they MUST be persisted explicitly or payroll never sees them.

Design: call calculate_bonus(dry_run=True) purely for the ROUTING DECISION
(auto-approve vs FC/GM/CEO), then always create a Bonus Approval Request with the
correct status. This is faithful to "reuse the approval spine" while guaranteeing
every earned naira has an immutable, payable record.
"""

import frappe
from frappe.utils import now_datetime, add_to_date

CHAMPION_UPSELL = "Upsell"
CHAMPION_DPSR = "DPSR"
CHAMPION_REVIVAL = "Customer Revival"
CHAMPION_CART = "Abandoned Cart"

_SYSTEM_APPROVER = "Administrator"


def _employee_for_rep(telesales_rep: str):
    """Map a Telesales Closer -> its active VV Employee (payroll identity).

    The ONLY mapping is VV Employee.linked_closer (confirmed schema). If no
    employee is linked, the bonus cannot be attributed to payroll. Rather than
    silently drop money, we log LOUDLY and the caller records the reason. Fix the
    data (create the VV Employee + set linked_closer); do not invent a fallback.
    """
    if not telesales_rep:
        return None
    emp = frappe.db.get_value(
        "VV Employee", {"linked_closer": telesales_rep, "is_active": 1}, "name"
    )
    if not emp:
        frappe.log_error(
            f"Loop5: no active VV Employee linked to closer '{telesales_rep}'. "
            f"Champion bonus NOT created. Create the employee and set "
            f"linked_closer + commission_eligible.",
            "Loop5 Unmapped Rep",
        )
    return emp


def unmapped_active_closers():
    """Return active Telesales Closers that have NO active VV Employee mapping.
    If this list is non-empty, those reps will earn nothing — used by VERIFY."""
    closers = frappe.get_all("Telesales Closer", filters={"is_active": 1},
                             fields=["name"])
    unmapped = []
    for c in closers:
        if not frappe.db.get_value(
            "VV Employee", {"linked_closer": c.name, "is_active": 1}, "name"):
            unmapped.append(c.name)
    return unmapped


def already_emitted(champion_type: str, source_event: str) -> bool:
    """One Bonus Event per source event, ever (idempotent history)."""
    return bool(frappe.db.exists(
        "Bonus Approval Request",
        {"champion_type": champion_type, "source_event": source_event},
    ))


def emit_bonus_event(telesales_rep: str, champion_type: str, amount: float,
                     source_event: str, justification: str = "",
                     dry_run: bool = False) -> dict:
    """Create the Bonus Event (a Bonus Approval Request) for an earned champion
    bonus, routed by the existing thresholds. Idempotent per source_event and
    concurrency-guarded by a row lock on the dedupe check.
    """
    from vitalvida.telesales_scoring import calculate_bonus

    result = {"emitted": False, "reason": None, "employee": None,
              "amount": amount, "request": None}

    if not amount or float(amount) <= 0:
        result["reason"] = "zero_amount"
        return result

    employee = _employee_for_rep(telesales_rep)
    if not employee:
        result["reason"] = "no_employee_for_rep"
        return result
    result["employee"] = employee

    # Routing decision only (never persists). Gives auto_approved / approver_role.
    routing = calculate_bonus(employee, "Telesales", float(amount), dry_run=True)
    result["routing"] = routing

    if dry_run:
        result["reason"] = "dry_run"
        return result

    # Concurrency + idempotency: lock any existing row for this source_event.
    # If one already exists (another worker beat us), do nothing.
    existing = frappe.db.sql(
        """SELECT name FROM `tabBonus Approval Request`
           WHERE champion_type = %s AND source_event = %s
           LIMIT 1 FOR UPDATE""",
        (champion_type, source_event),
    )
    if existing:
        result["reason"] = "already_emitted"
        result["request"] = existing[0][0]
        return result

    auto = bool(routing.get("auto_approved"))
    doc = frappe.get_doc({
        "doctype": "Bonus Approval Request",
        "employee": employee,
        "employee_type": "Telesales",
        "bonus_amount": float(amount),
        "champion_type": champion_type,
        "source_event": source_event,
        "l5_paid": 0,
        "justification": justification or f"{champion_type} champion bonus",
        "status": "Approved" if auto else "Pending",
        "required_approver_role": None if auto else routing.get("approver_role"),
    })
    if auto:
        # System auto-approval below the FC threshold — immutable and payable.
        doc.approved_by = _SYSTEM_APPROVER
        doc.approved_at = now_datetime()
    else:
        expiry_days = _expiry_days()
        doc.expires_at = add_to_date(now_datetime(), days=expiry_days)
    doc.insert(ignore_permissions=True)

    result.update({"emitted": True, "reason": None, "request": doc.name,
                   "auto_approved": auto})
    return result


def _expiry_days() -> int:
    try:
        from vitalvida.vitalvida.doctype.vv_commission_settings.vv_commission_settings import (
            get_commission_settings,
        )
        s = get_commission_settings()
        return int(getattr(s, "bonus_approval_expiry_days", None) or 7)
    except Exception:
        return 7
