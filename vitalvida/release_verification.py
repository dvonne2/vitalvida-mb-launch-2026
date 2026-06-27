import frappe
from datetime import timedelta
from frappe.utils import now_datetime, get_datetime

# Verification Office hours (24h clock). Office is OPEN Mon-Fri, OPEN_HOUR <= t < CLOSE_HOUR.
OFFICE_OPEN_HOUR = 10
OFFICE_CLOSE_HOUR = 17


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


def is_verification_office_open(when=None):
    """True only when the Verification Office can verify: Mon-Fri, 10:00 <= t < 17:00."""
    dt = get_datetime(when) if when else now_datetime()
    if dt.weekday() >= 5:
        return False
    if dt.hour < OFFICE_OPEN_HOUR:
        return False
    if dt.hour >= OFFICE_CLOSE_HOUR:
        return False
    return True


def next_verification_opening(released_at):
    """Next moment the Verification Office is open, at or after released_at.

    Pure time calculation. It does NOT enforce business policy (da.py decides
    whether a release is allowed). Its only contract: 'when is the office next
    available for this timestamp?'
    """
    dt = get_datetime(released_at)
    # Defensive guard. In normal operation da.py prevents in-hours releases, so
    # this branch should not fire for the release pathway. Keep it so the
    # function stays correct for manual fixes, imports, APIs and future callers:
    # if the office is already open, the honest answer is "now".
    if is_verification_office_open(dt):
        return dt
    cand = dt.replace(hour=OFFICE_OPEN_HOUR, minute=0, second=0, microsecond=0)
    if cand <= dt:
        cand = (cand + timedelta(days=1)).replace(hour=OFFICE_OPEN_HOUR, minute=0, second=0, microsecond=0)
    while cand.weekday() >= 5:
        cand = (cand + timedelta(days=1)).replace(hour=OFFICE_OPEN_HOUR, minute=0, second=0, microsecond=0)
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
            if now < next_verification_opening(o.released_at):
                continue
            from vitalvida.recovery import open_recovery_case
            open_recovery_case(o.name)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Loop1 Verification Check Error")
