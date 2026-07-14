"""Package 10 payroll orchestration.

Authoritative business records:
- Payroll Run Event: immutable computation snapshot / projection container.
- Payroll Approval Event: immutable approval fact; its one consequence is a Journal Entry.
- Payroll Payment Event: immutable payment-authorisation fact; its one consequence is a Payment Entry.

Package 10 explicitly owns only these two dedicated payroll consequence writers.
Package 08 remains owner of generic order/payment/liability finance consequences.
"""
import json
import frappe
from frappe.utils import flt, nowdate, now_datetime
from vitalvida.integration.idempotency import ensure_once, source_key
from vitalvida.integration.consequence import link_consequence

EVT_PAYROLL_APPROVED = "vv.payroll.approval_recorded"
EVT_PAYROLL_PAID = "vv.payroll.payment_recorded"
APPROVAL_WRITER = "vitalvida.finance.consequences.on_payroll_approved"
PAYMENT_WRITER = "vitalvida.finance.consequences.on_payroll_paid"


def _cfg():
    from vitalvida.finance.config import get_config
    return get_config()


def _lock_run(name):
    rows = frappe.db.sql("SELECT name FROM `tabPayroll Run Event` WHERE name=%s FOR UPDATE", (name,))
    if not rows:
        frappe.throw(f"Payroll Run Event {name} not found")
    return frappe.get_doc("Payroll Run Event", name)


@frappe.whitelist()
def compute_run(payroll_period: str, run_type: str = "Monthly Base + Earnings"):
    from vitalvida.payroll_events.generators import bridge_approved_bonuses
    bridge_approved_bonuses()
    key = source_key("prun", payroll_period, run_type)
    frappe.db.sql("SELECT GET_LOCK(%s, 15)", (f"vv:{key}",))
    try:
        existing = frappe.db.get_value("Payroll Run Event", {"idempotency_key": key}, "name")
        if existing:
            return {"name": existing, "created": False}
        include_base = run_type == "Monthly Base + Earnings"
        employees = frappe.get_all("VV Employee", filters={"is_active": 1}, fields=["name","employee_name","base_salary"])
        lines=[]; claims=[]
        for emp in employees:
            earnings = frappe.db.sql("""
                SELECT name, amount, earning_type FROM `tabCommission Earning Event`
                WHERE employee=%s AND status='Approved'
                  AND (payroll_run_ref IS NULL OR payroll_run_ref='')
                ORDER BY creation FOR UPDATE
            """, (emp.name,), as_dict=True)
            base = flt(emp.base_salary) if include_base else 0.0
            earn_total = sum(flt(e.amount) for e in earnings)
            if not base and not earnings: continue
            gross=base+earn_total
            paye=_paye_for(gross) if include_base else 0.0
            pension=_pension_for(base) if include_base else 0.0
            other=_pending_deductions(emp.name) if include_base else 0.0
            names=[e.name for e in earnings]; claims.extend(names)
            lines.append({"employee":emp.name,"employee_name":emp.employee_name,"base_salary":base,
              "earnings_total":earn_total,"earning_events_json":json.dumps(names),"gross_pay":gross,
              "paye":paye,"pension":pension,"other_deductions":other,
              "net_pay":max(round(gross-paye-pension-other,2),0)})
        if not lines: frappe.throw(f"No payable lines for {payroll_period} ({run_type}).")
        run=frappe.get_doc({"doctype":"Payroll Run Event","payroll_period":payroll_period,"run_type":run_type,
             "status":"Computed","idempotency_key":key,"lines":lines}).insert(ignore_permissions=True)
        for e_name in claims:
            frappe.db.sql("""UPDATE `tabCommission Earning Event` SET payroll_run_ref=%s
                WHERE name=%s AND (payroll_run_ref IS NULL OR payroll_run_ref='')""", (run.name,e_name))
            if frappe.db.sql("SELECT ROW_COUNT()")[0][0] != 1:
                frappe.throw(f"Concurrent payroll claim detected for earning {e_name}; transaction aborted.")
        return {"name":run.name,"created":True}
    finally:
        frappe.db.sql("SELECT RELEASE_LOCK(%s)", (f"vv:{key}",))


def _paye_for(monthly_gross):
    from vitalvida.payroll import compute_paye
    return round(compute_paye(monthly_gross*12)/12.0,2)

def _pension_for(base):
    from vitalvida.vitalvida.doctype.incentive_rule_version.incentive_rule_version import resolve
    rule=resolve("employee_pension_rate")
    return round(flt(base)*flt(rule.percentage)/100.0,2)

def _pending_deductions(employee):
    if not frappe.db.exists("DocType","Salary Deduction"): return 0.0
    return round(sum(flt(a) for a in frappe.get_all("Salary Deduction",filters={"employee":employee,"status":"Pending"},pluck="amount")),2)


@frappe.whitelist()
def approve_run(run_name: str, evidence_json: str = "{}"):
    run=_lock_run(run_name)
    if run.status!="Computed": frappe.throw(f"Run is {run.status}; only Computed runs approve.")
    run.assert_distinct_approver()
    key=source_key(EVT_PAYROLL_APPROVED,run.name)
    res=ensure_once("Payroll Approval Event",{"idempotency_key":key},{"doctype":"Payroll Approval Event",
        "payroll_run":run.name,"approved_by":frappe.session.user,"approved_at":now_datetime(),
        "evidence_json":evidence_json or "{}","idempotency_key":key})
    event=frappe.get_doc("Payroll Approval Event",res["name"])
    run.db_set({"approval_event":event.name,"finance_approved_by":event.approved_by,
                "finance_approved_at":event.approved_at,"status":"Approved"})
    from vitalvida.integration.outbox import enqueue, process_pending
    enqueue(EVT_PAYROLL_APPROVED,"Payroll Approval Event",event.name,APPROVAL_WRITER)
    process_pending(limit=10)
    return event.name


def post_journal_entry(source_doctype, source_name, event_key):
    """Compatibility alias; Package 08 is the sole accounting-document writer."""
    from vitalvida.finance.consequences import on_payroll_approved
    return on_payroll_approved(source_doctype, source_name, event_key)


def _payroll_legs(cfg,run):
    for f,label in (("salary_expense_account","Salaries & Wages"),("bonus_expense_account","Performance Bonuses"),
      ("paye_payable_account","PAYE Payable"),("pension_payable_account","Pension Payable"),
      ("net_wages_payable_account","Net Wages Payable"),("deductions_payable_account","Employee Deductions Payable")):
        if not cfg.get(f): frappe.throw(f"VV Finance Config lacks {f} ({label}).")
    base=sum(flt(l.base_salary) for l in run.lines); earn=sum(flt(l.earnings_total) for l in run.lines); legs=[]
    if base: legs.append({"account":cfg.salary_expense_account,"debit_in_account_currency":base,"cost_center":cfg.cost_center})
    if earn: legs.append({"account":cfg.bonus_expense_account,"debit_in_account_currency":earn,"cost_center":cfg.cost_center})
    # v1.1.1 (blocker B6): other deductions credit their OWN payable account.
    # Crediting them to Net Wages Payable stranded a permanent orphaned credit
    # there every period, since pay_run clears only total_net.
    for total,field in ((run.total_paye,"paye_payable_account"),(run.total_pension,"pension_payable_account"),
                        (run.total_other_deductions,"deductions_payable_account"),(run.total_net,"net_wages_payable_account")):
        if flt(total): legs.append({"account":cfg.get(field),"credit_in_account_currency":flt(total),"cost_center":cfg.cost_center})
    if round(sum(flt(x.get("debit_in_account_currency")) for x in legs)-sum(flt(x.get("credit_in_account_currency")) for x in legs),2)!=0:
        frappe.throw("Payroll Journal Entry does not balance.")
    return legs


def _mark_deductions_processed(run):
    if not frappe.db.exists("DocType","Salary Deduction"): return
    for l in run.lines:
        if flt(l.other_deductions):
            for n in frappe.get_all("Salary Deduction",filters={"employee":l.employee,"status":"Pending"},pluck="name"):
                frappe.db.set_value("Salary Deduction",n,{"status":"Processed","deduction_date":nowdate()})


@frappe.whitelist()
def pay_run(run_name: str, bank_reference: str, evidence_json: str = "{}"):
    run=_lock_run(run_name)
    if run.status!="Posted": frappe.throw(f"Run is {run.status}; pay only Posted runs.")
    if not (bank_reference or "").strip(): frappe.throw("Bank reference required.")
    approval=run.approval_event
    if not approval: frappe.throw("Payroll run has no authoritative approval event.")
    key=source_key(EVT_PAYROLL_PAID,run.name,bank_reference.strip())
    res=ensure_once("Payroll Payment Event",{"idempotency_key":key},{"doctype":"Payroll Payment Event",
       "payroll_run":run.name,"approval_event":approval,"amount":flt(run.total_net),"bank_reference":bank_reference.strip(),
       "paid_by":frappe.session.user,"paid_at":now_datetime(),"evidence_json":evidence_json or "{}","idempotency_key":key})
    event=frappe.get_doc("Payroll Payment Event",res["name"])
    run.db_set("payment_event",event.name)
    from vitalvida.integration.outbox import enqueue, process_pending
    enqueue(EVT_PAYROLL_PAID,"Payroll Payment Event",event.name,PAYMENT_WRITER)
    process_pending(limit=10)
    return event.name


def post_payment_entry(source_doctype, source_name, event_key):
    """Compatibility alias; Package 08 is the sole accounting-document writer."""
    from vitalvida.finance.consequences import on_payroll_paid
    return on_payroll_paid(source_doctype, source_name, event_key)


