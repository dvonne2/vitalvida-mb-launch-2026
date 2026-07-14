"""Package 09 patch — settlement spine installation. Idempotent.

1. Register settlement events in the Event Ownership Register (one writer each).
2. Wire vv.order.closed -> emit_da_earning as an Event Consumer Map row so the
   Package 08 bridge fan-out includes settlement (consume, never re-detect).
3. Seed Incentive Rule Version ``da_delivery_fee v1`` at ₦2,500 — capturing the
   CURRENT hardcoded rate from api/da.py:398 as an effective-dated, versioned
   rule (R102). Changing the fee is henceforth a new version, not a code edit.
4. Add extra account fields to VV Finance Config (da_receivable_account,
   da_recovery_account, da_fee_item) via custom fields.
5. Register the twice-weekly batch scheduler (Mon+Thu, R4 SET-005).
6. Add a ``supplier`` link on Delivery Agent when absent (R41 SET-003).
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

EVENT_DEFINITIONS = [
    dict(event_key="vv.settlement.da_fee_earned", event_name="DA Fee Earned",
         bucket="B", authoritative_doctype="DA Earning Event",
         erpnext_consequence="Purchase Invoice",
         policy_ref="R39 SET-001 / R40 SET-002",
         writer="vitalvida.settlement.engine.emit_da_earning"),
    dict(event_key="vv.settlement.batch_approved", event_name="Settlement Approved",
         bucket="C", authoritative_doctype="Settlement Batch",
         erpnext_consequence="Purchase Invoice",
         policy_ref="R43 SET-007 / R2 SET-004",
         writer="vitalvida.settlement.engine.finance_approve"),
    dict(event_key="vv.settlement.batch_paid", event_name="Settlement Paid",
         bucket="C", authoritative_doctype="Settlement Batch",
         erpnext_consequence="Payment Entry",
         policy_ref="R44 SET-008",
         writer="vitalvida.settlement.engine.pay_batch"),
    dict(event_key="vv.settlement.remittance_outstanding",
         event_name="Outstanding Remittance", bucket="B",
         authoritative_doctype="Outstanding Remittance Event",
         erpnext_consequence="Journal Entry",
         policy_ref="R46 SET-010",
         writer="vitalvida.settlement.engine.raise_outstanding_remittance"),
]


def execute():
    _register_events()
    _wire_closure_consumer()
    _seed_delivery_fee_rule()
    _extend_finance_config()
    _extend_delivery_agent()
    _register_batch_job()


def _register_events():
    if not frappe.db.exists("DocType", "Event Definition"):
        frappe.throw("Package 01 Event Definition missing; install it first.")
    meta = frappe.get_meta("Event Definition")
    for d in EVENT_DEFINITIONS:
        if frappe.db.exists("Event Definition", {"event_key": d["event_key"]}):
            continue
        row = {"doctype": "Event Definition", "is_active": 1}
        row.update({k: v for k, v in d.items() if meta.has_field(k)})
        frappe.get_doc(row).insert(ignore_permissions=True)


def _wire_closure_consumer():
    if not frappe.db.exists("DocType", "Event Consumer Map"):
        return
    meta = frappe.get_meta("Event Consumer Map")
    if not meta.has_field("consumer_method"):
        return
    method = "vitalvida.settlement.engine.emit_da_earning"

    def _row_fields():
        row = {"consumer_method": method}
        if meta.has_field("consumer_module"):
            row["consumer_module"] = "vitalvida.settlement"
        if meta.has_field("read_mode"):
            row["read_mode"] = "Reads authoritative record"
        if meta.has_field("delivery"):
            row["delivery"] = "Async (outbox)"
        if meta.has_field("is_active"):
            row["is_active"] = 1
        return row

    if getattr(meta, "istable", 0):
        # child model: append under the vv.order.closed Event Definition
        if not frappe.db.exists("DocType", "Event Definition"):
            return
        ed_meta = frappe.get_meta("Event Definition")
        if not ed_meta.has_field("consumers"):
            return
        ed_name = frappe.db.get_value(
            "Event Definition", {"event_key": "vv.order.closed"}, "name")
        if not ed_name:
            return
        ed = frappe.get_doc("Event Definition", ed_name)
        for r in (ed.get("consumers") or []):
            if r.get("consumer_method") == method:
                return
        ed.append("consumers", _row_fields())
        ed.save(ignore_permissions=True)
    else:
        # flat model: standalone event-keyed row
        if frappe.db.exists("Event Consumer Map",
                            {"event_key": "vv.order.closed",
                             "consumer_method": method}):
            return
        row = {"doctype": "Event Consumer Map", "event_key": "vv.order.closed"}
        row.update(_row_fields())
        frappe.get_doc(row).insert(ignore_permissions=True)


def _seed_delivery_fee_rule():
    if frappe.db.exists("Incentive Rule Version", {"rule_key": "da_delivery_fee"}):
        return
    frappe.get_doc({
        "doctype": "Incentive Rule Version", "rule_key": "da_delivery_fee",
        "version": 1, "rule_type": "Flat Amount", "amount": 2500,
        "effective_from": "2026-01-01", "is_active": 1,
        "notes": "v1 captures the pre-Package-09 hardcoded rate "
                 "(api/da.py get_da_stats base_per_order=2500). Rate changes "
                 "are new versions (R102 PAY-011)."}).insert(
        ignore_permissions=True)


def _extend_finance_config():
    if not frappe.db.exists("DocType", "VV Finance Config"):
        frappe.throw("VV Finance Config missing — install Package 08 first.")
    create_custom_fields({"VV Finance Config": [
        {"fieldname": "da_receivable_account", "fieldtype": "Link",
         "options": "Account", "label": "DA Receivable (remittances)"},
        {"fieldname": "da_recovery_account", "fieldtype": "Link",
         "options": "Account", "label": "DA Shortage Recovery Account"},
        {"fieldname": "da_fee_item", "fieldtype": "Link", "options": "Item",
         "label": "DA Fee Service Item"},
    ]}, ignore_validate=True)


def _extend_delivery_agent():
    if not frappe.db.exists("DocType", "Delivery Agent"):
        return
    if frappe.get_meta("Delivery Agent").has_field("supplier"):
        return
    create_custom_fields({"Delivery Agent": [
        {"fieldname": "supplier", "fieldtype": "Link", "options": "Supplier",
         "label": "Supplier (payable party, R41)", "read_only": 1},
    ]}, ignore_validate=True)


def _register_batch_job():
    method = "vitalvida.settlement.engine.build_settlement_batches"
    if frappe.db.exists("Scheduled Job Type", {"method": method}):
        return
    frappe.get_doc({"doctype": "Scheduled Job Type", "method": method,
                    "frequency": "Cron", "cron_format": "0 6 * * 1,4",
                    "stopped": 0}).insert(ignore_permissions=True)
