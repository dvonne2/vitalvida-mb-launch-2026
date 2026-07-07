"""
Loop 4 — Customer Outcome Engine (NEW, central).
Measures whether the customer achieved WHY they bought, not just THAT they bought.
Feeds Trust, Health, Education, Referral/Advocacy eligibility, and Relationship NBA.
Bootstrap: with no evidence -> Insufficient Data (never assume success from a sale).
"""
import frappe
from frappe.utils import now_datetime

CATEGORY_OUTCOME = {
    "Haircare": "Healthier, fuller hair",
    "Jewellery": "Satisfied with quality and appearance",
    "Supplements": "Achieved intended wellness outcome",
    "General": "Satisfied with the product",
}


def set_outcome(customer, product_category="General", status="Pending",
                measured_via="", evidence="", desired_outcome=""):
    """Create/append a Customer Outcome record and reflect status on the profile."""
    if not frappe.db.exists("Customer Profile", customer):
        return None
    doc = frappe.get_doc({
        "doctype": "Customer Outcome", "customer": customer,
        "product_category": product_category,
        "desired_outcome": desired_outcome or CATEGORY_OUTCOME.get(product_category, ""),
        "outcome_status": status, "measured_via": measured_via,
        "measured_at": now_datetime() if status != "Pending" else None,
        "evidence": evidence, "influences_trust": 1,
    })
    doc.insert(ignore_permissions=True)
    prof = frappe.get_doc("Customer Profile", customer)
    prof.outcome_status = status
    prof.save(ignore_permissions=True)
    # outcome achievement is a strong trust signal
    if status in ("Achieved", "Exceeded"):
        from vitalvida.customer_relationship.trust import apply_trust_signal
        apply_trust_signal(customer, "Outcome Achieved", ref_name=doc.name,
                           reason="Customer achieved desired outcome")
    return doc.name


def infer_outcome_from_repeat(customer):
    """A repeat delivered+paid purchase is soft evidence the prior outcome was achieved."""
    from vitalvida.customer_relationship.identity import orders_for_customer, is_delivered_and_paid
    dp = [o for o in orders_for_customer(customer) if is_delivered_and_paid(o)]
    if len(dp) >= 2:
        return set_outcome(customer, status="Achieved", measured_via="Repeat Purchase",
                           evidence="Customer reordered after a delivered+paid order.")
    return None
