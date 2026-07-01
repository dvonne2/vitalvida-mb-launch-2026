"""
Loop 4 — Customer Success (computed; lives as FIELDS on Customer Profile, no separate
doctype). Law 10: customer success creates growth. Success is DERIVED from Outcome +
Health + Trust + open complaints + education progress + NBA — a single visible,
reportable state per customer.

States: On Track / Needs Attention / At Risk / Succeeded / Churned.
Bootstrap-aware: with too little signal it stays 'Needs Attention' rather than guessing.
Never optimizes for revenue (that is Loop 5).
"""
import frappe
from frappe.utils import now_datetime, cint


def compute_success(customer):
    """Return {state, score, reason}. Pure derivation; reads other computed signals."""
    if not frappe.db.exists("Customer Profile", customer):
        return {"state": None, "score": None, "reason": "No profile"}
    p = frappe.get_doc("Customer Profile", customer)

    open_complaints = frappe.db.count("Customer Complaint",
        {"customer": customer, "status": ["in", ["Open", "Acknowledged", "In Progress", "Reopened"]]})

    trust = cint(p.trust_score)
    health = p.health_band or "Insufficient Data"
    outcome = p.outcome_status or "Unknown"

    # ----- hard states first -----
    # Churned: relationship lost / critical health with no recent success
    if p.relationship_status in ("Lost", "Deleted") or health == "Critical":
        state, score = "Churned", 10
        return {"state": state, "score": score,
                "reason": f"health={health}, status={p.relationship_status}"}

    # Succeeded: achieved outcome AND healthy AND trusted AND no open issues
    if outcome in ("Achieved","Exceeded") and health == "Healthy" and trust >= 70 and not open_complaints:
        return {"state": "Succeeded", "score": 95,
                "reason": "Outcome achieved, healthy, trusted, no open complaints."}

    # ----- graded states -----
    score = 50
    reasons = []
    if outcome in ("Achieved","Exceeded"):
        score += 20; reasons.append(f"outcome {outcome.lower()}")
    elif outcome == "Partially Achieved":
        score += 8; reasons.append("partially achieved")
    elif outcome == "Working Toward":
        score += 3; reasons.append("working toward")
    elif outcome == "Not Achieved":
        score -= 20; reasons.append("outcome NOT achieved")
    if health == "Healthy":
        score += 15; reasons.append("healthy")
    elif health == "Watch":
        score -= 5; reasons.append("watch")
    elif health == "At Risk":
        score -= 20; reasons.append("at risk")
    if trust >= 70:
        score += 10; reasons.append(f"trust {trust}")
    elif trust < 45:
        score -= 10; reasons.append(f"low trust {trust}")
    if open_complaints:
        score -= 25; reasons.append(f"{open_complaints} open complaint(s)")

    score = max(0, min(100, score))
    if open_complaints or health == "At Risk" or outcome == "Not Achieved":
        state = "At Risk"
    elif score >= 70:
        state = "On Track"
    else:
        state = "Needs Attention"
    return {"state": state, "score": score, "reason": "; ".join(reasons) or "baseline"}


def _confidence(customer):
    """Confidence in the success assessment grows with evidence (orders + signals)."""
    from vitalvida.customer_relationship.identity import orders_for_customer, is_delivered_and_paid
    dp = len([o for o in orders_for_customer(customer) if is_delivered_and_paid(o)])
    reviews = frappe.db.count("Customer Review", {"customer": customer})
    outcomes = frappe.db.count("Customer Outcome", {"customer": customer})
    evidence = dp + reviews + outcomes
    if evidence >= 4:
        return "High"
    if evidence >= 2:
        return "Medium"
    return "Low"


def refresh_success(customer):
    from frappe.utils import add_to_date
    r = compute_success(customer)
    if r["state"] is None:
        return r
    conf = _confidence(customer)
    # next check sooner for at-risk customers, later for stable ones
    horizon = 7 if r["state"] in ("At Risk", "Churned") else (14 if r["state"] == "Needs Attention" else 30)
    frappe.db.set_value("Customer Profile", customer, {
        "customer_success_state": r["state"],
        "customer_success_score": r["score"],
        "success_reason": r["reason"][:140],
        "success_confidence": conf,
        "success_last_evaluated_at": now_datetime(),
        "next_success_check": add_to_date(now_datetime(), days=horizon),
    })
    r["confidence"] = conf
    return r
