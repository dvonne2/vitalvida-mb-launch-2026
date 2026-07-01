"""
Loop 4 — Customer Relationship API (whitelisted).
Read endpoints are read-only. Write endpoints are role-gated. Nothing here touches
Loop 1-3 except to read, moves no stock, sends through existing notifications only.
"""
import frappe
from vitalvida.customer_relationship import identity as I
from vitalvida.customer_relationship.reviews import review_summary as _review_summary


def _require_role():
    roles = set(frappe.get_roles(frappe.session.user))
    if not ({"System Manager", "Customer Success Manager"} & roles):
        frappe.throw("Not permitted: requires System Manager or Customer Success Manager.",
                     frappe.PermissionError)


@frappe.whitelist()
def get_customer_360(phone):
    """READ-ONLY. Full relationship view for a customer by (any-format) phone."""
    key = I.normalize_phone(phone)
    if not key or not frappe.db.exists("Customer Profile", key):
        return {"found": False, "normalized_phone": key,
                "note": "No Customer Profile for this phone."}
    prof = frappe.get_doc("Customer Profile", key).as_dict()
    from vitalvida.customer_relationship.timeline import get_timeline
    return {
        "found": True, "profile": prof,
        "timeline": get_timeline(key, limit=50),
        "orders": I.orders_for_customer(key),
        "complaints": frappe.get_all("Customer Complaint", filters={"customer": key},
            fields=["name","category","severity","status","opened_at","resolution_hours"]),
        "reviews": frappe.get_all("Customer Review", filters={"customer": key},
            fields=["rating","sentiment","received_at","review_text"]),
        "referrals": frappe.get_all("Customer Referral", filters={"referrer": key},
            fields=["referral_code","status","referred_name","created_at"]),
        "review_summary": _review_summary(key),
        "advocacy": frappe.get_all("Customer Advocacy", filters={"customer": key},
            fields=["advocacy_type","status","advocacy_score","detail","recognized_at"]),
    }


@frappe.whitelist()
def get_relationship_brief(phone):
    """READ-ONLY. Relationship AI brief (dormant unless AI configured)."""
    key = I.normalize_phone(phone)
    if not key:
        return {"error": "Unresolvable phone"}
    from vitalvida.customer_relationship.ai import relationship_brief
    return relationship_brief(key)


@frappe.whitelist()
def get_ignored_customers(days=None):
    """READ-ONLY. Customers with no contact beyond the ignored threshold (Law 6)."""
    d = int(days) if days else int(frappe.db.get_single_value("Loop 4 Settings","ignored_days_threshold") or 30)
    rows = frappe.db.sql("""
        SELECT name, customer_name, last_contact_date, last_order_date, trust_score, health_band
        FROM `tabCustomer Profile`
        WHERE do_not_contact = 0
          AND COALESCE(last_contact_date, last_order_date) IS NOT NULL
          AND DATEDIFF(CURDATE(), DATE(COALESCE(last_contact_date, last_order_date))) >= %s
        ORDER BY COALESCE(last_contact_date, last_order_date) ASC
    """, (d,), as_dict=True)
    return {"threshold_days": d, "count": len(rows), "customers": rows}


@frappe.whitelist()
def recompute_customer(phone):
    """WRITE (Loop 4 records only). Recompute one customer's relationship state."""
    _require_role()
    key = I.normalize_phone(phone)
    if not key or not frappe.db.exists("Customer Profile", key):
        return {"error": "No profile for phone", "normalized": key}
    from vitalvida.customer_relationship.profile import recompute_profile
    return recompute_profile(key)


@frappe.whitelist()
def run_relationship_refresh(limit=0):
    """WRITE (Loop 4 records only). Recompute all profiles. Role-gated. Idempotent."""
    _require_role()
    from vitalvida.customer_relationship.runner import run_relationship_refresh as run
    return run(limit=int(limit) if limit else 0)


@frappe.whitelist()
def file_complaint(phone, category, description="", severity="Medium", source_order=""):
    """WRITE. Open a complaint; links to Escalation Request pattern, applies trust signal."""
    _require_role()
    key = I.resolve_customer(phone, create=True)
    if not key:
        return {"error": "Unresolvable phone"}
    from frappe.utils import now_datetime
    doc = frappe.get_doc({
        "doctype": "Customer Complaint", "customer": key, "opened_at": now_datetime(),
        "category": category, "severity": severity, "status": "Open",
        "description": description, "source_order": source_order,
    })
    doc.insert(ignore_permissions=True)
    from vitalvida.customer_relationship.trust import apply_trust_signal
    apply_trust_signal(key, "Complaint Filed", ref_name=doc.name, reason=f"{category} complaint")
    from vitalvida.customer_relationship.timeline import record_event
    record_event(key, "Complaint Filed", summary=f"{category} ({severity})",
                 ref_doctype="Customer Complaint", ref_name=doc.name)
    return {"complaint": doc.name, "status": "Open"}


@frappe.whitelist()
def submit_review(phone, rating=None, review_text="", channel="", source_order=""):
    """WRITE. Capture a customer review (derives sentiment, feeds trust + outcome)."""
    _require_role()
    key = I.resolve_customer(phone, create=True)
    if not key:
        return {"error": "Unresolvable phone"}
    from vitalvida.customer_relationship.reviews import record_review
    return record_review(key, rating=rating, review_text=review_text,
                         channel=channel, source_order=source_order)


@frappe.whitelist()
def get_review_candidates():
    """READ-ONLY. Customers eligible to be ASKED for a review (success, no review, no open complaint)."""
    from vitalvida.customer_relationship.reviews import should_request_review
    out = []
    for p in frappe.get_all("Customer Profile", fields=["name", "customer_name"]):
        ok, reason = should_request_review(p["name"])
        if ok:
            out.append({"customer": p["name"], "name": p["customer_name"]})
    return {"count": len(out), "candidates": out}
