"""
Loop 4 — Relationship Next Best Action (Law 3). RELATIONSHIP actions only, never
sales/upsell (that is Loop 5). Pure recommendation; writes a profile hint + log row.
"""
import frappe
from frappe.utils import now_datetime, date_diff, nowdate, get_datetime, cint
import hashlib


def compute_nba(customer):
    """Return {action, priority, reason}. Relationship-focused, bootstrap-aware."""
    from vitalvida.customer_relationship.identity import orders_for_customer, is_delivered_and_paid
    if not frappe.db.exists("Customer Profile", customer):
        return {"action": "Insufficient Data", "priority": "None", "reason": "No profile"}
    prof = frappe.get_doc("Customer Profile", customer)

    # 1. Open complaint always wins — service recovery before anything
    if frappe.db.count("Customer Complaint",
            {"customer": customer, "status": ["in", ["Open","Acknowledged","In Progress","Reopened"]]}):
        return {"action": "Resolve Open Complaint", "priority": "Critical",
                "reason": "Customer has an unresolved complaint."}

    orders = orders_for_customer(customer)
    dp = [o for o in orders if is_delivered_and_paid(o)]

    # 2. Brand new / no orders
    if not orders:
        return {"action": "Welcome New Customer", "priority": "High",
                "reason": "Profile exists with no orders yet."}

    # 2b. Delivered but NOT paid -> cash risk; resolve before anything else relationship-y
    if any(o.get("delivered_at") and not is_delivered_and_paid(o) for o in orders):
        return {"action": "Follow Up Delivery", "priority": "Critical",
                "reason": "Delivered but payment not confirmed — resolve before other actions."}

    # 3. Recently delivered+paid but outcome unknown -> check outcome
    if dp and prof.outcome_status in (None, "", "Unknown", "Pending"):
        return {"action": "Check Outcome", "priority": "High",
                "reason": "Delivered+paid but we haven't confirmed the customer's outcome."}

    # 4. Ignored clock (Law 6)
    ignored_days = cint(frappe.db.get_single_value("Loop 4 Settings", "ignored_days_threshold") or 30)
    last_contact = prof.last_contact_date or prof.last_order_date
    if last_contact and date_diff(nowdate(), str(last_contact)[:10]) >= ignored_days:
        return {"action": "Re-Engage (Ignored)", "priority": "High",
                "reason": f"No contact in {date_diff(nowdate(), str(last_contact)[:10])}d (Law 6)."}

    # 5. Eligible for referral ask (Law 7)
    from vitalvida.customer_relationship.referral import is_referral_eligible
    ok, _ = is_referral_eligible(customer)
    if ok and not frappe.db.count("Customer Referral", {"referrer": customer}):
        return {"action": "Ask For Referral", "priority": "Medium",
                "reason": "Trust earned, no open issues, no referral asked yet (Law 7)."}

    # 6. Dormant nurture
    if prof.relationship_status in ("Dormant", "At Risk"):
        return {"action": "Nurture Dormant", "priority": "Medium",
                "reason": f"Relationship status is {prof.relationship_status}."}

    # 7. Invite a review after a success if none exists
    if dp and not frappe.db.count("Customer Review", {"customer": customer}):
        return {"action": "Invite Review", "priority": "Low",
                "reason": "Successful order with no review yet."}

    return {"action": "No Action Needed", "priority": "None",
            "reason": "Relationship is healthy with no pending relationship action."}


def refresh_nba(customer):
    r = compute_nba(customer)
    prof = frappe.get_doc("Customer Profile", customer)
    prof.next_best_action = r["action"]
    prof.nba_reason = r["reason"][:140]
    prof.save(ignore_permissions=True)
    idem = hashlib.sha1(f"{customer}|{r['action']}|{nowdate()}".encode()).hexdigest()
    if not frappe.db.get_value("Relationship NBA Log", {"idempotency_key": idem}, "name"):
        frappe.get_doc({
            "doctype": "Relationship NBA Log", "customer": customer,
            "generated_at": now_datetime(), "action": r["action"],
            "priority": r["priority"], "status": "Suggested",
            "reason": r["reason"], "idempotency_key": idem,
        }).insert(ignore_permissions=True)
    return r
