"""Package 08 patch — idempotent installation of the finance consequence spine.

1. Unique ``vv_source_event_key`` custom field on Payment Entry, Sales Invoice
   and Journal Entry — the idempotency anchor for every consequence writer.
2. Consequence typed-link fields (Package 01 spec) on the source events:
   Payment Reconciliation Log and Order Closure Event (when present).
3. Event Definitions registered in the Event Ownership Register (GOV-002):
   one authoritative writer per consequence.
4. Freeze the legacy Profit First mutable balance: bucket ``current_balance``
   becomes read-only metadata; balances are henceforth derived from GL (R63).

Safe to re-run; creates nothing twice; deletes nothing.
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CONSEQUENCE_DOCTYPES = ["Payment Entry", "Sales Invoice", "Journal Entry"]

EVENT_DEFINITIONS = [
    dict(event_key="vv.finance.payment_confirmed",
         event_name="Payment Confirmed", bucket="B",
         authoritative_doctype="Payment Reconciliation Log",
         erpnext_consequence="Payment Entry",
         policy_ref="R51 FIN-001 / R52 FIN-002",
         writer="vitalvida.finance.consequences.on_payment_confirmed"),
    dict(event_key="vv.order.closed",
         event_name="Order Closed", bucket="B",
         authoritative_doctype="Order Closure Event",
         erpnext_consequence="Sales Invoice",
         policy_ref="R89 ORD-007 / R53 FIN-003",
         writer="vitalvida.finance.consequences.on_order_closed"),
    dict(event_key="vv.finance.liability_approved",
         event_name="Liability Approved", bucket="B",
         authoritative_doctype="Outstanding Remittance Event",
         erpnext_consequence="Journal Entry",
         policy_ref="R46 SET-010 / R60",
         writer="vitalvida.finance.consequences.on_liability_approved"),
    dict(event_key="vv.finance.profit_first_allocated",
         event_name="Profit First Allocated", bucket="B",
         authoritative_doctype="Order Closure Event",
         erpnext_consequence="Journal Entry",
         policy_ref="R63 FIN-013",
         writer="vitalvida.finance.profit_first_gl.on_order_closed_allocate"),
]


def execute():
    _install_source_key_fields()
    _install_consequence_links()
    _register_events()
    _freeze_bucket_balance()
    _register_bridge_job()


def _register_bridge_job():
    """Data-only scheduling (Scheduled Job Type) — no hooks.py edit required."""
    method = "vitalvida.finance.bridge.run"
    if frappe.db.exists("Scheduled Job Type", {"method": method}):
        return
    frappe.get_doc({
        "doctype": "Scheduled Job Type",
        "method": method,
        "frequency": "Cron",
        "cron_format": "*/5 * * * *",
        "stopped": 0,
    }).insert(ignore_permissions=True)


def _install_source_key_fields():
    mapping = {}
    for dt in CONSEQUENCE_DOCTYPES:
        if frappe.db.exists("DocType", dt):
            mapping[dt] = [{
                "fieldname": "vv_source_event_key", "fieldtype": "Data",
                "label": "VV Source Event Key", "unique": 1, "read_only": 1,
                "no_copy": 1, "search_index": 1,
                "description": "Idempotency key of the VitalVida event that "
                               "authored this document (Package 08).",
            }]
    if mapping:
        create_custom_fields(mapping, ignore_validate=True)


def _install_consequence_links():
    from vitalvida.integration.consequence import make_consequence_custom_fields
    for dt in ("Payment Reconciliation Log", "Order Closure Event"):
        if frappe.db.exists("DocType", dt):
            create_custom_fields(make_consequence_custom_fields(dt),
                                 ignore_validate=True)


def _register_events():
    if not frappe.db.exists("DocType", "Event Definition"):
        frappe.throw("Event Definition (Package 01) missing — Package 08 "
                     "cannot register writers. Install Package 01 first.")
    meta = frappe.get_meta("Event Definition")
    for d in EVENT_DEFINITIONS:
        if frappe.db.exists("Event Definition", {"event_key": d["event_key"]}):
            continue
        row = {"doctype": "Event Definition", "is_active": 1}
        for k, v in d.items():
            if meta.has_field(k):
                row[k] = v
        frappe.get_doc(row).insert(ignore_permissions=True)


def _freeze_bucket_balance():
    """R63: no mutable Profit First balance. Field made read-only via Property
    Setter (reversible; rollback deletes the setter)."""
    if not frappe.db.exists("DocType", "Profit First Bucket"):
        return
    from frappe.custom.doctype.property_setter.property_setter import (
        make_property_setter)
    make_property_setter("Profit First Bucket", "current_balance",
                         "read_only", 1, "Check",
                         validate_fields_for_doctype=False)
