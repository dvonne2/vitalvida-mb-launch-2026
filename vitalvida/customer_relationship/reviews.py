"""
Loop 4 — Reviews Engine.
Captures customer reviews, derives sentiment, feeds Trust and Outcome, records on the
timeline, and decides WHEN to request a review (only after a delivered+paid success and
no open complaint — never spam). Recommendation only; sends through notifications.py.
"""
import frappe
from frappe.utils import now_datetime, cint


def _sentiment_from_rating(rating):
    r = cint(rating)
    if r >= 4:
        return "Positive"
    if r == 3:
        return "Neutral"
    return "Negative"


def record_review(customer, rating=None, review_text="", channel="", source_order=""):
    """Capture a review, derive sentiment, apply trust signal, record on timeline."""
    if not frappe.db.exists("Customer Profile", customer):
        return {"error": "No profile"}
    sentiment = _sentiment_from_rating(rating) if rating else None
    doc = frappe.get_doc({
        "doctype": "Customer Review", "customer": customer, "received_at": now_datetime(),
        "rating": str(rating) if rating else None, "sentiment": sentiment,
        "channel": channel, "source_order": source_order, "review_text": review_text,
        "influences_trust": 1,
    })
    doc.insert(ignore_permissions=True)

    # sentiment feeds Trust (positive earns, negative damages)
    from vitalvida.customer_relationship.trust import apply_trust_signal
    if sentiment == "Positive":
        apply_trust_signal(customer, "Review Positive", ref_name=doc.name, reason="Positive review")
    elif sentiment == "Negative":
        apply_trust_signal(customer, "Review Negative", ref_name=doc.name, reason="Negative review")

    # a positive review is soft evidence the customer achieved their outcome (Law 9)
    if sentiment == "Positive":
        prof = frappe.get_doc("Customer Profile", customer)
        if prof.outcome_status in (None, "", "Unknown", "Pending"):
            from vitalvida.customer_relationship.outcome import set_outcome
            set_outcome(customer, status="Achieved", measured_via="Review",
                        evidence=f"Positive review (rating {rating}).")

    from vitalvida.customer_relationship.timeline import record_event
    record_event(customer, "Review Received",
                 summary=f"{sentiment or 'Review'} (rating {rating})" if rating else "Review",
                 ref_doctype="Customer Review", ref_name=doc.name, channel=channel)
    return {"review": doc.name, "sentiment": sentiment}


def should_request_review(customer):
    """
    Decide if it's appropriate to ASK for a review: >=1 delivered+paid order, no open
    complaint, and no review captured yet. Returns (bool, reason). Never spams.
    """
    from vitalvida.customer_relationship.identity import orders_for_customer, is_delivered_and_paid
    if not frappe.db.exists("Customer Profile", customer):
        return False, "No profile"
    prof = frappe.get_doc("Customer Profile", customer)
    if prof.do_not_contact:
        return False, "Opted out"
    dp = [o for o in orders_for_customer(customer) if is_delivered_and_paid(o)]
    if not dp:
        return False, "No delivered+paid order yet"
    if frappe.db.count("Customer Review", {"customer": customer}):
        return False, "Review already captured"
    open_complaints = frappe.db.count("Customer Complaint",
        {"customer": customer, "status": ["in", ["Open","Acknowledged","In Progress","Reopened"]]})
    if open_complaints:
        return False, "Resolve open complaint before requesting a review"
    return True, "Eligible: successful order, no review yet, no open issues"


def review_summary(customer):
    """Aggregate a customer's reviews (count, avg rating, sentiment mix)."""
    rows = frappe.get_all("Customer Review", filters={"customer": customer},
                          fields=["rating", "sentiment"])
    if not rows:
        return {"count": 0, "avg_rating": None, "sentiment": {}}
    ratings = [cint(r["rating"]) for r in rows if r.get("rating")]
    mix = {}
    for r in rows:
        s = r.get("sentiment") or "Unknown"
        mix[s] = mix.get(s, 0) + 1
    return {"count": len(rows),
            "avg_rating": round(sum(ratings)/len(ratings), 2) if ratings else None,
            "sentiment": mix}
