"""Package 16B — Chart of Accounts + Profit First (7 buckets).

Adds the Profit First configuration fields, moves allocation from Order Closed to
Payment Confirmed, and retires the legacy parallel bucket/wallet ledger.

Creates NO accounts and posts NOTHING. The chart is imported by an explicit,
authorised action (finance.chart_of_accounts.dry_run then install), because the
target company and the account map are accountant decisions (R126).

Profit First stays inert twice over:
  * `enable_profit_first_gl` is untouched (off unless already on);
  * `profit_first_mode` defaults to Shadow, which computes the proposed split
    and returns it WITHOUT posting.
Percentages are deliberately left unset: they must total exactly 100% or the
allocator refuses, which is the forcing function for a real decision.
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

METHOD = "vitalvida.finance.profit_first_gl.on_payment_confirmed_allocate"
LEGACY = "vitalvida.finance.profit_first_gl.on_order_closed_allocate"


def execute():
    _extend_config()
    _repoint_event_definition()
    _retire_legacy_parallel_ledger()


def _extend_config():
    if not frappe.db.exists("DocType", "VV Finance Config"):
        frappe.throw("VV Finance Config missing — install Package 08 first.")
    create_custom_fields({"VV Finance Config": [
        {"fieldname": "profit_first_mode", "fieldtype": "Select",
         "options": "Shadow\nActive", "default": "Shadow",
         "label": "Profit First Mode",
         "description": "Shadow computes and returns the proposed split without "
                        "posting. Active posts the allocation Journal Entry."},
        {"fieldname": "pf_growth_account", "fieldtype": "Link", "options": "Account",
         "label": "Profit First — Growth Reserve"},
        {"fieldname": "pf_payroll_account", "fieldtype": "Link", "options": "Account",
         "label": "Profit First — Payroll Reserve"},
        {"fieldname": "pf_refund_account", "fieldtype": "Link", "options": "Account",
         "label": "Profit First — Refund Reserve"},
        {"fieldname": "pf_pct_growth", "fieldtype": "Percent",
         "label": "Profit First % — Growth Reserve"},
        {"fieldname": "pf_pct_payroll", "fieldtype": "Percent",
         "label": "Profit First % — Payroll Reserve"},
        {"fieldname": "pf_pct_refund", "fieldtype": "Percent",
         "label": "Profit First % — Refund Reserve"},
    ]}, ignore_validate=True)


def _repoint_event_definition():
    """Profit First allocates cash when cash arrives, not when an order closes."""
    if not frappe.db.exists("DocType", "Event Definition"):
        return
    name = frappe.db.get_value("Event Definition",
                               {"event_key": "vv.finance.profit_first_allocated"}, "name")
    if not name:
        return
    meta = frappe.get_meta("Event Definition")
    values = {}
    if meta.has_field("authoritative_doctype"):
        values["authoritative_doctype"] = "Payment Reconciliation Log"
    if meta.has_field("producer_module"):
        values["producer_module"] = "vitalvida.finance"
    if meta.has_field("policy_ref"):
        values["policy_ref"] = "R63 FIN-013 / Package 16B"
    if values:
        frappe.db.set_value("Event Definition", name, values, update_modified=False)


def _retire_legacy_parallel_ledger():
    """Make the old mutable bucket/wallet balances non-operational. Keeps history."""
    for dt in ("Profit First Bucket", "Profit First Wallet"):
        if not frappe.db.exists("DocType", dt):
            continue
        if frappe.get_meta(dt).has_field("is_active"):
            frappe.db.set_value(dt, {}, "is_active", 0, update_modified=False)
