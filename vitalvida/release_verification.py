import frappe
from datetime import timedelta
from frappe.utils import now_datetime, get_datetime


def require_payment_proof(order_id):
    """True only if a usable Payment Proof (with a screenshot) exists for the order."""
    rows = frappe.get_all(
        "Payment Proof",
        filters={"order": order_id, "proof_status": ["in", ["Submitted", "Verified"]]},
        fields=["name", "payment_screenshot"],
    )
    for r in rows:
        if (r.get("payment_screenshot") or "").strip():
            return True
    return False


def next_verification_morning(released_at):
    """Next Monday-Friday 10:00 AM at or after released_at. Weekend releases roll to Monday."""
    dt = get_datetime(released_at)
    cand = dt.replace(hour=10, minute=0, second=0, microsecond=0)
    if cand <= dt:
        cand = (cand + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    while cand.weekday() >= 5:
        cand = (cand + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    return cand


def check_release_verification():
    """Scheduled hourly. Opens a Recovery Case for any release past its deadline."""
    now = now_datetime()
    orders = frappe.get_all(
        "VV Order",
        filters={"order_status": "Released - Payment Evidence", "payment_confirmed": 0},
        fields=["name", "released_at"],
    )
    for o in orders:
        try:
            if not o.released_at:
                continue
            if now < next_verification_morning(o.released_at):
                continue
            from vitalvida.recovery import open_recovery_case
            open_recovery_case(o.name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Loop1 Verification Check Error")
