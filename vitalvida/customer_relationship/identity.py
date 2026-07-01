"""
VitalVida Loop 4 — Customer Relationship Engine
identity.py — Customer identity & phone normalization (FOUNDATION)

Identity is keyed on the canonical phone 234XXXXXXXXXX, intentionally mirroring
vitalvida/normalise.py and vitalvida/notifications.py:_format_phone so Loop 4 keys
match how VV Order.customer_phone is already stored and how messages are addressed.
Loop 4 does NOT modify VV Order and adds no Link field to it (reads only).
"""
import frappe


def normalize_phone(phone):
    """Canonicalize to 234XXXXXXXXXX. Returns None if unresolvable (never fabricate a key)."""
    if not phone:
        return None
    d = "".join(filter(str.isdigit, str(phone)))
    if not d:
        return None
    if d.startswith("0") and len(d) == 11:
        d = "234" + d[1:]
    elif len(d) == 10:
        d = "234" + d
    if d.startswith("234") and len(d) == 13:
        return d
    return None


def resolve_customer(phone, name=None, email=None, create=True):
    """Resolve phone -> Customer Profile name (the canonical phone). None if unresolvable."""
    key = normalize_phone(phone)
    if not key:
        return None
    if frappe.db.exists("Customer Profile", key):
        if name or email:
            doc = frappe.get_doc("Customer Profile", key)
            ch = False
            if name and not doc.customer_name:
                doc.customer_name = name; ch = True
            if email and not doc.primary_email:
                doc.primary_email = email; ch = True
            if ch:
                doc.save(ignore_permissions=True)
        return key
    if not create:
        return None
    frappe.get_doc({
        "doctype": "Customer Profile", "phone": key,
        "customer_name": name or "", "primary_email": email or "",
        "lifecycle_stage": "Prospect", "relationship_status": "Active",
    }).insert(ignore_permissions=True)
    return key


def orders_for_customer(phone_key):
    """All VV Orders for a canonical phone (read-only). Loop 4's join into Loop 1."""
    if not phone_key:
        return []
    return frappe.get_all(
        "VV Order", filters={"customer_phone": phone_key},
        fields=["name", "order_status", "package_name", "total_payable", "product_amount",
                "is_upsell", "creation", "paid_at", "delivered_at", "payment_confirmed",
                "state", "lga", "customer_tier", "customer_name", "customer_email"],
        order_by="creation asc")


# DPSR / success is owned by Loop 1+dsr.py. Loop 4 only READS the order's
# delivered+paid status; it never redefines what 'success' means.
def is_delivered_and_paid(order_row):
    """A single order is a relationship success iff delivered AND paid. Read-only helper."""
    return bool(order_row.get("delivered_at")) and (
        bool(order_row.get("paid_at")) or bool(order_row.get("payment_confirmed"))
        or order_row.get("order_status") == "Paid")
