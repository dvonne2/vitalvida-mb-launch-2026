"""
Loop 4 — Trust Engine (Law 2). Trust 0-100, audited via Customer Trust Log.
Deltas are CONFIGURABLE in Loop 4 Settings (business can tune without code changes).
New customers start PROVISIONAL at 50. Never optimizes for revenue.
"""
import frappe
from frappe.utils import now_datetime, cint

# signal -> Loop 4 Settings fieldname. Defaults applied if settings/field missing.
SIGNAL_FIELD = {
    "Delivered": ("trust_delivered", 5), "On-Time Delivery": ("trust_on_time", 3),
    "Payment Completed": ("trust_payment", 4), "Repeat Purchase": ("trust_repeat", 8),
    "Referral Made": ("trust_referral", 6), "Outcome Achieved": ("trust_outcome", 10),
    "Review Positive": ("trust_review_pos", 5),
    "Complaint Filed": ("trust_complaint", -6), "Delivery Failed": ("trust_delivery_failed", -8),
    "Order Cancelled": ("trust_cancelled", -4), "SLA Breach": ("trust_sla_breach", -5),
    "Review Negative": ("trust_review_neg", -7), "Provisional Init": (None, 0),
}
PROVISIONAL_START = 50


def _delta(signal):
    field, default = SIGNAL_FIELD.get(signal, (None, 0))
    if not field:
        return default
    try:
        v = frappe.db.get_single_value("Loop 4 Settings", field)
        return cint(v) if v else default
    except Exception:
        return default


def _provisional_start():
    try:
        v = frappe.db.get_single_value("Loop 4 Settings", "trust_provisional_start")
        return cint(v) if v else PROVISIONAL_START
    except Exception:
        return PROVISIONAL_START


def _band(score, provisional=False):
    if provisional:
        return "Provisional"
    if score >= 85: return "Very High"
    if score >= 70: return "High"
    if score >= 45: return "Medium"
    return "Low"


def apply_trust_signal(customer, signal, ref_name="", reason=""):
    if not frappe.db.exists("Customer Profile", customer):
        return None
    delta = _delta(signal)
    prof = frappe.get_doc("Customer Profile", customer)
    cur = cint(prof.trust_score) or _provisional_start()
    new = max(0, min(100, cur + delta))
    provisional = cint(prof.delivered_paid_orders) < 1 and signal != "Outcome Achieved"
    frappe.get_doc({
        "doctype": "Customer Trust Log", "customer": customer,
        "change_time": now_datetime(), "signal": signal, "delta": delta,
        "score_after": new, "ref_name": ref_name, "reason": reason,
    }).insert(ignore_permissions=True)
    prof.trust_score = new
    prof.trust_band = _band(new, provisional)
    prof.save(ignore_permissions=True)
    return new


def recompute_trust(customer):
    from vitalvida.customer_relationship.identity import orders_for_customer, is_delivered_and_paid
    if not frappe.db.exists("Customer Profile", customer):
        return None
    score = _provisional_start()
    dp = 0
    for o in orders_for_customer(customer):
        # Trust accrues ONLY on a completed relationship event: delivered AND paid.
        # Delivered-only is not trust — for pay-on-delivery it is cash risk, not a
        # successful outcome. So neither delivered_at alone nor payment alone earns
        # trust; only the two together do.
        if is_delivered_and_paid(o):
            score += _delta("Delivered") + _delta("Payment Completed"); dp += 1
        if o.get("order_status") == "Cancelled":
            score += _delta("Order Cancelled")
    if dp >= 2:
        score += _delta("Repeat Purchase") * (dp - 1)
    score = max(0, min(100, score))
    prof = frappe.get_doc("Customer Profile", customer)
    prof.trust_score = score
    prof.trust_band = _band(score, provisional=(dp < 1))
    prof.save(ignore_permissions=True)
    return score
