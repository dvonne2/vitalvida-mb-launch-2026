"""
Loop 4 — Advocacy Engine. Identifies and tracks customers strong enough to become
advocates/ambassadors. Score = composite of trust + converted referrals + repeat
successes + positive reviews. Recommendation only — never auto-enrolls a customer into
outbound; it flags eligibility and records WHY. Records advocacy recognition on the
timeline. Reads Loop 1 orders; writes only Loop 4 records.
"""
import frappe
from frappe.utils import now_datetime, cint

ELIGIBLE_SCORE = 75
ELIGIBLE_TRUST = 80


def compute_advocacy(customer):
    """Return {score, reason, signals} — the composite advocacy assessment."""
    from vitalvida.customer_relationship.identity import orders_for_customer, is_delivered_and_paid
    if not frappe.db.exists("Customer Profile", customer):
        return None
    prof = frappe.get_doc("Customer Profile", customer)
    trust = cint(prof.trust_score)
    converted_refs = frappe.db.count("Customer Referral",
        {"referrer": customer, "status": ["in", ["Converted", "First Order", "Signed Up"]]})
    dp = len([o for o in orders_for_customer(customer) if is_delivered_and_paid(o)])
    pos_reviews = frappe.db.count("Customer Review", {"customer": customer, "sentiment": "Positive"})

    score = min(100, int(trust * 0.45 + converted_refs * 15 + max(0, dp - 1) * 10 + pos_reviews * 8))
    signals = {"trust": trust, "converted_referrals": converted_refs,
               "repeat_successes": max(0, dp - 1), "positive_reviews": pos_reviews}
    bits = []
    if trust >= ELIGIBLE_TRUST: bits.append(f"high trust ({trust})")
    if converted_refs: bits.append(f"{converted_refs} converted referral(s)")
    if dp >= 2: bits.append(f"{dp} delivered+paid orders")
    if pos_reviews: bits.append(f"{pos_reviews} positive review(s)")
    reason = "; ".join(bits) or f"trust {trust}, no advocacy signals yet"
    return {"score": score, "reason": reason, "signals": signals}


def refresh_advocacy(customer):
    """Update profile advocacy_eligible + upsert a Customer Advocacy record with the why."""
    a = compute_advocacy(customer)
    if a is None:
        return None
    prof = frappe.get_doc("Customer Profile", customer)
    eligible = a["score"] >= ELIGIBLE_SCORE and cint(prof.trust_score) >= ELIGIBLE_TRUST
    prof.advocacy_eligible = 1 if eligible else 0
    prof.save(ignore_permissions=True)

    existing = frappe.db.get_value("Customer Advocacy",
        {"customer": customer, "status": ["in", ["Identified", "Invited", "Active"]]}, "name")
    if eligible:
        if existing:
            frappe.db.set_value("Customer Advocacy", existing,
                {"advocacy_score": a["score"], "detail": a["reason"][:140]})
        else:
            doc = frappe.get_doc({
                "doctype": "Customer Advocacy", "customer": customer,
                "recognized_at": now_datetime(), "advocacy_type": "Repeat Promoter",
                "status": "Identified", "advocacy_score": a["score"],
                "detail": a["reason"][:140],
            })
            doc.insert(ignore_permissions=True)
            from vitalvida.customer_relationship.timeline import record_event
            record_event(customer, "Other", summary="Identified as potential advocate",
                         detail=a["reason"], ref_doctype="Customer Advocacy", ref_name=doc.name,
                         source="Loop 4")
    elif existing:
        # no longer meets the bar (e.g. trust dropped) — mark inactive, keep history
        frappe.db.set_value("Customer Advocacy", existing,
            {"status": "Inactive", "advocacy_score": a["score"], "detail": a["reason"][:140]})
    return {"score": a["score"], "eligible": eligible, "reason": a["reason"]}
