"""Seed / re-point the Event Ownership Register for Packages 04-07.

Configuration only (same discipline as Package 01's seed): one row per event
TYPE, no occurrence backfill. Existing rows E2/E3/E12 are re-pointed to their
new authoritative doctypes; new operational events E15-E17, E24-E27 are added.

Depends on Package 01 (Event Definition + vitalvida.integration.*).
"""
import frappe
from vitalvida.integration.idempotency import ensure_once

EVENTS = [
    # (key, name, bucket, authority, source_key_field, producer, consequence,
    #  policy, notes)
    ("E2_INVENTORY_FULFILLED", "Inventory Fulfilled", "B", "Fulfilment Event",
     "order", "domain/fulfilment.py", "Delivery Note -> SLE",
     "INV-004, INV-005",
     "Re-pointed from DA Stock Entry: the Delivered+Paid rule now emits a "
     "first-class Fulfilment Event whose consequence is the Delivery Note."),
    ("E3_ORDER_CLOSED", "Order Closed", "B", "Order Closure Event",
     "order", "domain/fulfilment.py", "Sales Invoice / GL",
     "ORD-007",
     "Re-pointed from Order Status Log: closure is now a first-class event "
     "asserting all five ORD-007 conditions; SI/GL posted by Package 08."),
    ("E12_DELIVERY_COMPLETED", "Delivery Completed", "B", "VV Order",
     "order", "domain/orders.py (transition)", "Delivery Note (via E2)",
     "DA-005, ORD-006",
     "DA-005: photo POD not required; DA mark / telesales confirmation "
     "completes delivery. The Delivered transition is the event; proof "
     "demand remains a separate accountability workflow."),
    ("E15_ORDER_CREATED", "Order Created/Confirmed", "B", "VV Order",
     "order", "domain/orders.py (transition)", "Sales Order",
     "ORD-001, ORD-002, ORD-003",
     "Intake stays custom (pre-SO); confirmation posts the ERPNext Sales "
     "Order exactly once (po_no = order name)."),
    ("E16_ORDER_CANCELLED", "Order Cancelled", "B", "Order Cancellation Event",
     "order", "domain/orders.py", "SO/DN/SI cancellation (standard reversal)",
     "ORD-009",
     "Never delete orders; the event triggers standard ERPNext reversals."),
    ("E17_UPSELL_APPLIED", "Upsell Applied", "B", "Order Amendment",
     "order", "domain/orders.py", "Sales Order (versioned amendment)",
     "ORD-005, PAY-007",
     "Versioned amendment before fulfilment; original and final preserved."),
    ("E19_STOCK_RETURNED", "Stock Returned by DA", "B",
     "Inventory Custody Event", "source_name", "domain/logistics.py",
     "Stock Entry (Material Transfer DA->Returns)",
     "INV-010, INV-009",
     "Return leg of the custody chain plus the ERPNext transfer."),
    ("E24_DELIVERY_ATTEMPT_FAILED", "Delivery Attempt Failed", "C",
     "Delivery Attempt Event", "order", "domain/orders.py", "",
     "ORD-010, DA-006",
     "Immutable attempt event; no revenue, no fee, custody unchanged."),
    ("E25_CUSTODY_TRANSFERRED", "Custody Transferred", "B",
     "Inventory Custody Event", "source_name", "domain/logistics.py",
     "Stock Entry (Material Transfer)",
     "LOG-001, LOG-002, LOG-003, LOG-006, INV-002, INV-003",
     "One event per acknowledged leg: Main->Logistics(Transit), "
     "Logistics->DA, DA->Returns. Carriers never custodians."),
    ("E26_TRANSPORT_COST_INCURRED", "Transport Cost Incurred", "A",
     "Journal Entry", "", "domain/finance_contract.py (Package 08 hand-off)",
     "Journal Entry (or Purchase Invoice where supplier invoice exists)",
     "LOG-005",
     "Bucket A authority is the standard ERPNext accounting document. "
     "Stock Dispatch is source evidence only; Package 08 posts and links the "
     "Journal Entry or Purchase Invoice. No custom accounting ledger."),
    ("E27_DA_APPROVED", "Delivery Agent Approved", "B", "DA Application",
     "", "domain/delivery_agents.py",
     "User + Contact + Address + Warehouse + Supplier (provisioned once)",
     "DA-002, DA-003, INV-001, SET-003",
     "Approval is the single idempotent provisioning event (DA-003)."),
]


def execute():
    if not frappe.db.exists("DocType", "Event Definition"):
        frappe.throw("Package 01 (Event Definition) must be installed first.")
    for (key, name, bucket, auth_dt, srckey, producer, conseq, policy,
         notes) in EVENTS:
        active = 1
        if auth_dt and not frappe.db.exists("DocType", auth_dt):
            active = 0
            notes = (f"[INACTIVE: authority DocType '{auth_dt}' not "
                     f"installed] " + notes)
        res = ensure_once(
            "Event Definition", {"event_key": key},
            {"event_key": key, "event_name": name, "bucket": bucket,
             "authoritative_doctype": auth_dt or None,
             "source_key_field": srckey or None,
             "producer_module": producer,
             "erpnext_consequence": conseq or None,
             "policy_ref": policy, "is_active": active, "notes": notes})
        if not res["created"]:
            d = frappe.get_doc("Event Definition", res["name"])
            d.update({"event_name": name, "bucket": bucket,
                      "authoritative_doctype": auth_dt or None,
                      "source_key_field": srckey or None,
                      "producer_module": producer,
                      "erpnext_consequence": conseq or None,
                      "policy_ref": policy, "is_active": active,
                      "notes": notes})
            d.save(ignore_permissions=True)
