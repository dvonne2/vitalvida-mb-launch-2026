"""
Loop 4 — Referral Engine (Law 7: ask for referrals ONLY after trust is earned).
This is the CUSTOMER referral system — distinct from the affiliate (aff_id) system.
Eligibility gate: >=1 delivered+paid order AND trust >= threshold AND no open complaint.
"""
import frappe
from frappe.utils import now_datetime, cint
import hashlib


def is_referral_eligible(customer):
    """Law 7 gate. Returns (bool, reason)."""
    from vitalvida.customer_relationship.identity import orders_for_customer, is_delivered_and_paid
    if not frappe.db.exists("Customer Profile", customer):
        return False, "No profile"
    prof = frappe.get_doc("Customer Profile", customer)
    if prof.do_not_contact:
        return False, "Customer opted out of contact"
    dp = [o for o in orders_for_customer(customer) if is_delivered_and_paid(o)]
    if len(dp) < 1:
        return False, "No delivered+paid order yet — trust not earned"
    threshold = cint(frappe.db.get_single_value("Loop 4 Settings", "referral_trust_threshold") or 70)
    if cint(prof.trust_score) < threshold:
        return False, f"Trust {cint(prof.trust_score)} below referral threshold {threshold}"
    # People recommend OUTCOMES, not transactions (Law 9). Require an achieved outcome.
    if prof.outcome_status not in ("Achieved", "Exceeded"):
        return False, f"Outcome is '{prof.outcome_status or 'Unknown'}' — ask only after the customer has achieved their outcome"
    open_complaints = frappe.db.count("Customer Complaint",
                       {"customer": customer, "status": ["in", ["Open","Acknowledged","In Progress","Reopened"]]})
    if open_complaints:
        return False, f"{open_complaints} open complaint(s) — resolve before asking"
    return True, "Eligible: trust earned, no open issues"


def refresh_referral_eligibility(customer):
    ok, reason = is_referral_eligible(customer)
    prof = frappe.get_doc("Customer Profile", customer)
    prof.referral_eligible = 1 if ok else 0
    prof.save(ignore_permissions=True)
    return ok, reason


def _gen_code(customer):
    return "REF-" + hashlib.sha1(f"{customer}|{now_datetime()}".encode()).hexdigest()[:8].upper()


def create_referral(referrer, referred_phone=None, referred_name=None):
    """Create a referral ONLY if the referrer is eligible (enforces Law 7)."""
    ok, reason = is_referral_eligible(referrer)
    if not ok:
        return {"created": False, "reason": reason}
    from vitalvida.customer_relationship.identity import normalize_phone
    doc = frappe.get_doc({
        "doctype": "Customer Referral", "referrer": referrer, "created_at": now_datetime(),
        "referral_code": _gen_code(referrer),
        "referred_phone": normalize_phone(referred_phone) or (referred_phone or ""),
        "referred_name": referred_name or "", "status": "Invited",
    })
    doc.insert(ignore_permissions=True)
    from vitalvida.customer_relationship.trust import apply_trust_signal
    apply_trust_signal(referrer, "Referral Made", ref_name=doc.name, reason="Made a referral")
    return {"created": True, "referral": doc.name, "code": doc.referral_code}
