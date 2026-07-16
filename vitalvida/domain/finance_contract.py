"""Package 08 hand-off contract for E1.

Packages 04-07 do not post Payment Entry or GL. This consumer validates the
payment authority and leaves the durable E1 source/outbox record available for
Package 08 to replace/extend with the accounting consequence.
"""
import frappe


def on_payment_confirmed(source_doctype, source_name, event_key):
    if source_doctype != "Payment Reconciliation Log":
        frappe.throw("E1 Finance contract requires Payment Reconciliation Log authority.")
    row = frappe.db.get_value(source_doctype, source_name,
                              ["order", "reconciliation_status"], as_dict=True)
    if not row or not row.order:
        frappe.throw(f"Invalid E1 source {source_name}: no order.")
    if row.reconciliation_status not in ("Auto Confirmed", "Manually Confirmed", "Confirmed"):
        frappe.throw(f"Invalid E1 source {source_name}: not confirmed.")
    # Intentionally no accounting document here. Package 08 owns that consequence.
    return {"event_key": event_key, "order": row.order,
            "accounting_consequence": "DEFERRED_TO_PACKAGE_08"}


def on_transport_cost_incurred(source_doctype, source_name, event_key):
    if source_doctype != "Stock Dispatch":
        frappe.throw("E26 source evidence must be Stock Dispatch.")
    return {"event_key":event_key,"dispatch":source_name,"accounting_consequence":"DEFERRED_TO_PACKAGE_08"}
