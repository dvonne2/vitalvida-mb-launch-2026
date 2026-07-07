"""
Loop 4 — Customer Timeline & Memory (Law 4: permanent relationship memory).
Records relationship events idempotently. Reads Loop 1 orders; never writes them.
"""
import frappe
from frappe.utils import now_datetime, get_datetime
import hashlib


def record_event(customer, event_type, event_time=None, summary="", detail="",
                 ref_doctype="", ref_name="", channel="", source="Loop 4"):
    """Append an idempotent timeline event. Safe to call repeatedly."""
    if not customer:
        return None
    event_time = event_time or now_datetime()
    key_src = f"{customer}|{event_type}|{ref_name}|{str(event_time)[:10]}"
    idem = hashlib.sha1(key_src.encode()).hexdigest()
    existing = frappe.db.get_value("Customer Timeline Event", {"idempotency_key": idem}, "name")
    if existing:
        return existing
    doc = frappe.get_doc({
        "doctype": "Customer Timeline Event", "customer": customer,
        "event_time": event_time, "event_type": event_type, "summary": summary[:140],
        "detail": detail, "ref_doctype": ref_doctype, "ref_name": ref_name,
        "channel": channel, "source": source, "idempotency_key": idem,
    })
    doc.insert(ignore_permissions=True)
    return doc.name


def rebuild_timeline_from_orders(customer):
    """Backfill timeline from a customer's VV Orders (idempotent). Read Loop 1 only."""
    from vitalvida.customer_relationship.identity import orders_for_customer, is_delivered_and_paid
    n = 0
    for o in orders_for_customer(customer):
        record_event(customer, "Order Placed", o.get("creation"),
                     summary=f"Order {o['name']} — {o.get('package_name') or ''}",
                     ref_doctype="VV Order", ref_name=o["name"], source="Loop 1")
        n += 1
        if o.get("delivered_at"):
            record_event(customer, "Delivered", o.get("delivered_at"),
                         summary=f"Delivered {o['name']}", ref_doctype="VV Order",
                         ref_name=o["name"], source="Loop 1"); n += 1
        if is_delivered_and_paid(o):
            record_event(customer, "Paid", o.get("paid_at") or o.get("delivered_at"),
                         summary=f"Paid {o['name']}", ref_doctype="VV Order",
                         ref_name=o["name"], source="Loop 1"); n += 1
    return n


def get_timeline(customer, limit=100):
    """Read-only: a customer's relationship timeline, newest first."""
    return frappe.get_all("Customer Timeline Event", filters={"customer": customer},
        fields=["event_time","event_type","summary","channel","source","ref_name"],
        order_by="event_time desc", limit=limit)
