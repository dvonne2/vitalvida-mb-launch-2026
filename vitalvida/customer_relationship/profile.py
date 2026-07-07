"""
Loop 4 — Customer Profile aggregator / relationship orchestrator.
Recomputes the full relationship state for one customer by composing the engines.
Reads Loop 1 orders; writes only Loop 4 records. Bootstrap-aware throughout.
"""
import frappe
from frappe.utils import now_datetime, get_datetime, cint


def _lifecycle_from_history(dp_count, total_orders, trust, advocacy_eligible):
    if advocacy_eligible and trust >= 85:
        return "Advocate"
    if dp_count >= 4:
        return "Loyal"
    if dp_count >= 2:
        return "Repeat Customer"
    if dp_count == 1:
        return "First-Time Buyer"
    if total_orders >= 1:
        return "Prospect"
    return "Prospect"


def recompute_profile(customer):
    """Full recompute for one Customer Profile. Idempotent. Returns a summary dict."""
    from vitalvida.customer_relationship.identity import orders_for_customer, is_delivered_and_paid
    from vitalvida.customer_relationship import trust as T, health as H, nba as N
    from vitalvida.customer_relationship import referral as R, advocacy as A
    if not frappe.db.exists("Customer Profile", customer):
        return {"error": "No profile"}
    prof = frappe.get_doc("Customer Profile", customer)
    orders = orders_for_customer(customer)
    dp = [o for o in orders if is_delivered_and_paid(o)]

    # relationship memory aggregates
    if orders:
        prof.first_order_date = min(get_datetime(o["creation"]) for o in orders)
        prof.last_order_date = max(get_datetime(o["creation"]) for o in orders)
    prof.total_orders = len(orders)
    prof.delivered_paid_orders = len(dp)
    # Law 1: relationship_started = earliest of first order or existing value (never moves later)
    if orders and not prof.relationship_started:
        prof.relationship_started = min(get_datetime(o["creation"]) for o in orders)
    # last meaningful interaction = most recent order (orders are meaningful by definition);
    # complaints/reviews/referrals update this elsewhere when they occur
    if orders:
        prof.last_meaningful_interaction = max(get_datetime(o["creation"]) for o in orders)
    # roll up tier from most recent order that has one
    tier = next((o.get("customer_tier") for o in reversed(orders) if o.get("customer_tier")), None)
    if tier:
        prof.customer_tier = tier
    # enrich name/email if blank
    if not prof.customer_name:
        prof.customer_name = next((o.get("customer_name") for o in orders if o.get("customer_name")), "") or ""
    if not prof.primary_email:
        prof.primary_email = next((o.get("customer_email") for o in orders if o.get("customer_email")), "") or ""
    prof.save(ignore_permissions=True)

    # engines (each writes its own audit + reflects onto the profile)
    T.recompute_trust(customer)
    H.refresh_health(customer)
    R.refresh_referral_eligibility(customer)
    A.refresh_advocacy(customer)

    # lifecycle + relationship_status
    prof.reload()
    prof.lifecycle_stage = _lifecycle_from_history(
        len(dp), len(orders), cint(prof.trust_score), bool(prof.advocacy_eligible))
    if prof.health_band == "Critical":
        prof.relationship_status = "Dormant"
    elif prof.health_band == "At Risk":
        prof.relationship_status = "At Risk"
    elif prof.lifecycle_stage != "Prospect":
        prof.relationship_status = "Active"
    prof.save(ignore_permissions=True)

    # Education: observe the existing education journey and let completion nudge
    # Outcome/Trust (bind + influence; does not duplicate education_journey.py).
    try:
        from vitalvida.customer_relationship.education import sync_education
        sync_education(customer, customer_phone=prof.phone)
    except Exception:
        pass  # education binding must never block a recompute

    # Customer Success: derive the visible/reportable success state from Outcome +
    # Health + Trust + open complaints (fields on the profile, no separate doctype).
    from vitalvida.customer_relationship.success import refresh_success
    refresh_success(customer)

    N.refresh_nba(customer)

    # Start the lifelong customer relationship journey once the customer is a real
    # buyer (First-Time onward) and hasn't opted out. Idempotent: create_customer_journey
    # is duplicate-guarded (one active arc per customer). Scheduler ships OFF, so this
    # only seeds the arc; no message sends until the journey runner is enabled.
    try:
        if prof.lifecycle_stage != "Prospect" and not prof.do_not_contact:
            from vitalvida.customer_relationship.journey import create_customer_journey
            create_customer_journey(customer, customer_phone=prof.phone)
    except Exception:
        pass  # never let journey seeding block a profile recompute

    prof.reload()
    return {
        "customer": customer, "lifecycle_stage": prof.lifecycle_stage,
        "trust_score": prof.trust_score, "trust_band": prof.trust_band,
        "health_band": prof.health_band, "outcome_status": prof.outcome_status,
        "customer_success_state": prof.customer_success_state,
        "referral_eligible": bool(prof.referral_eligible),
        "advocacy_eligible": bool(prof.advocacy_eligible),
        "next_best_action": prof.next_best_action,
        "total_orders": prof.total_orders, "delivered_paid_orders": prof.delivered_paid_orders,
    }
