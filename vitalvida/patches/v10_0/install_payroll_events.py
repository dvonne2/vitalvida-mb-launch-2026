"""Package 10 patch — payroll & earnings spine. Idempotent.

1. Event Ownership Register entries (one writer each).
2. Event Consumer Map: vv.order.closed -> emit_telesales_commission (the
   Package 08 bridge fans out automatically — data-only wiring).
3. Rule seeds capturing today's hardcoded values as versioned rules (R102):
   telesales_commission (from VV Commission Settings when readable, else a
   disabled placeholder that forces explicit configuration),
   employee_pension_rate v1 = 8% (payroll.py hardcode),
   champion_bonus_passthrough v1 (bridge marker — amounts come from the BAR).
4. Payroll account fields + consequence-writer override on VV Finance Config.
5. Hourly Scheduled Job for the Bonus Approval Request bridge.
6. Freeze VV Employee.total_earned_ytd read-only (derived from events now).
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

EVENT_DEFINITIONS = [
    dict(event_key="vv.payroll.commission_earned",
         event_name="Employee Commission Earned", bucket="B",
         authoritative_doctype="Commission Earning Event",
         erpnext_consequence="Journal Entry (Additional Salary post-HRMS)",
         policy_ref="R96 PAY-004 / R19 PAY-002 / R102 PAY-011",
         writer="vitalvida.payroll_events.generators"),
    dict(event_key="vv.payroll.approval_recorded", event_name="Payroll Approval Recorded",
         bucket="B", authoritative_doctype="Payroll Approval Event",
         erpnext_consequence="Journal Entry",
         policy_ref="R95 PAY-003 / R104 PAY-013",
         writer="vitalvida.payroll_events.consequences.post_journal_entry"),
    dict(event_key="vv.payroll.payment_recorded", event_name="Payroll Payment Recorded",
         bucket="B", authoritative_doctype="Payroll Payment Event",
         erpnext_consequence="Payment Entry",
         policy_ref="R95 PAY-003 / R104 PAY-013",
         writer="vitalvida.payroll_events.consequences.post_payment_entry"),
]


def execute():
    _register_events()
    _wire_closure_consumer()
    _seed_rules()
    _extend_finance_config()
    _register_bridge_job()
    _freeze_ytd()


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
    method = "vitalvida.payroll_events.generators.emit_telesales_commission"

    def _row_fields():
        row = {"consumer_method": method}
        if meta.has_field("consumer_module"):
            row["consumer_module"] = "vitalvida.payroll_events"
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


def _seed_rules():
    seeds = []
    # telesales: capture live settings where possible
    amount, pct, rtype = 0, 0, None
    try:
        s = frappe.get_single("VV Commission Settings")
        if (s.get("telesales_commission_type") or "Per Order") == "Per Order":
            rtype, amount = "Flat Amount", float(
                s.get("telesales_commission_amount") or 0)
        else:
            rtype, pct = "Percentage", float(
                s.get("telesales_commission_pct") or 0)
    except Exception:
        pass
    seeds.append(dict(rule_key="telesales_commission", version=1,
                      rule_type=rtype or "Flat Amount", amount=amount,
                      percentage=pct, is_active=1 if (amount or pct) else 0,
                      notes="v1 captured from VV Commission Settings at "
                            "install; if inactive, no capture was possible — "
                            "configure explicitly before earnings flow "
                            "(refuse-not-guess, R102)."))
    seeds.append(dict(rule_key="employee_pension_rate", version=1,
                      rule_type="Percentage", percentage=8.0, is_active=1,
                      notes="v1 captures payroll.py hardcoded 8% employee "
                            "pension; accountant ratifies (R125)."))
    seeds.append(dict(rule_key="champion_bonus_passthrough", version=1,
                      rule_type="Parameterised", is_active=1,
                      parameters_json='{"basis": "Bonus Approval Request '
                                      'amount (existing single seam)"}',
                      notes="Bridge marker: amounts come from the approved "
                            "BAR, not this rule (Master v1.1: single-seam "
                            "layer preserved)."))
    for s in seeds:
        if frappe.db.exists("Incentive Rule Version",
                            {"rule_key": s["rule_key"]}):
            continue
        s.update({"doctype": "Incentive Rule Version",
                  "effective_from": "2026-01-01"})
        frappe.get_doc(s).insert(ignore_permissions=True)


def _extend_finance_config():
    if not frappe.db.exists("DocType", "VV Finance Config"):
        frappe.throw("VV Finance Config missing — install Package 08 first.")
    create_custom_fields({"VV Finance Config": [
        {"fieldname": "salary_expense_account", "fieldtype": "Link",
         "options": "Account", "label": "Salaries & Wages"},
        {"fieldname": "bonus_expense_account", "fieldtype": "Link",
         "options": "Account", "label": "Performance Bonuses"},
        {"fieldname": "paye_payable_account", "fieldtype": "Link",
         "options": "Account", "label": "PAYE Payable"},
        {"fieldname": "pension_payable_account", "fieldtype": "Link",
         "options": "Account", "label": "Pension Payable"},
        {"fieldname": "net_wages_payable_account", "fieldtype": "Link",
         "options": "Account", "label": "Net Wages Payable"},
        {"fieldname": "deductions_payable_account", "fieldtype": "Link",
         "options": "Account", "label": "Employee Deductions Payable",
         "description": "Payable account for non-statutory salary deductions "
                        "(loans, advances). v1.1.1: must NOT be Net Wages "
                        "Payable, or deduction credits strand there."},
        {"fieldname": "payroll_consequence_writer", "fieldtype": "Data",
         "label": "Payroll Consequence Writer",
         "description": "Dotted path; default posts a Journal Entry. Point at "
                        "an Additional Salary writer after HRMS adoption — "
                        "events and history are untouched."},
    ]}, ignore_validate=True)


def _register_bridge_job():
    method = "vitalvida.payroll_events.generators.bridge_approved_bonuses"
    if frappe.db.exists("Scheduled Job Type", {"method": method}):
        return
    frappe.get_doc({"doctype": "Scheduled Job Type", "method": method,
                    "frequency": "Hourly", "stopped": 0}).insert(
        ignore_permissions=True)


def _freeze_ytd():
    if not frappe.db.exists("DocType", "VV Employee"):
        return
    if not frappe.get_meta("VV Employee").has_field("total_earned_ytd"):
        return
    from frappe.custom.doctype.property_setter.property_setter import (
        make_property_setter)
    make_property_setter("VV Employee", "total_earned_ytd", "read_only", 1,
                         "Check", validate_fields_for_doctype=False)
