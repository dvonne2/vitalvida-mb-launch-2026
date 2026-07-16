"""Payroll read models — consume events and GL; never recompute pay.

    payroll_cost           GL (salary + bonus expense accounts), GL dates
    employee_ytd           derived by summing POSTED earning events + run
                           lines — replaces the stored mutable
                           VV Employee.total_earned_ytd (Gap P3); derived on
                           read, stored nowhere
    earnings_ledger        immutable Commission Earning Events for portals
                           (REP-004: dashboards show earning records, never a
                           live pay calc)
"""
import json

import frappe
from frappe.utils import flt, nowdate


def _cfg():
    from vitalvida.finance.config import get_config
    return get_config()


@frappe.whitelist()
def payroll_cost(from_date, to_date=None):
    cfg = _cfg()
    accounts = [a for a in (cfg.get("salary_expense_account"),
                            cfg.get("bonus_expense_account")) if a]
    if not accounts:
        return {"metric": "payroll_cost", "amount": None,
                "reason": "payroll expense accounts not configured; GL is the "
                          "only permitted source (R95) — no placeholder."}
    total = frappe.db.sql(
        """SELECT COALESCE(SUM(debit - credit), 0) FROM `tabGL Entry`
           WHERE is_cancelled = 0 AND company = %s
             AND account IN ({}) AND posting_date BETWEEN %s AND %s"""
        .format(", ".join(["%s"] * len(accounts))),
        tuple([cfg.company] + accounts + [from_date, to_date or nowdate()]))[0][0]
    return {"metric": "payroll_cost", "source": "GL Entry",
            "date_authority": "gl_posting_date", "amount": flt(total)}


@frappe.whitelist()
def employee_ytd(employee: str, year: str = None):
    """Derived YTD — replaces the stored running total (GOV-004/Gap P3)."""
    year = year or nowdate()[:4]
    rows = frappe.get_all(
        "Payroll Run Line",
        filters={"employee": employee, "parenttype": "Payroll Run Event"},
        fields=["parent", "gross_pay"])
    posted = {r.name for r in frappe.get_all(
        "Payroll Run Event",
        filters={"status": ("in", ["Posted", "Paid"]),
                 "payroll_period": ("like", f"{year}%")},
        fields=["name"])}
    total = sum(flt(r.gross_pay) for r in rows if r.parent in posted)
    return {"metric": "employee_ytd", "employee": employee, "year": year,
            "source": "Posted Payroll Run Events (derived, stored nowhere)",
            "amount": round(total, 2)}


@frappe.whitelist()
def earnings_ledger(employee: str, limit: int = 100):
    return frappe.get_all(
        "Commission Earning Event", filters={"employee": employee},
        fields=["name", "earning_type", "source_reference", "rule_version",
                "amount", "period", "status", "earned_at", "payroll_run_ref",
                "reversal_of"],
        order_by="earned_at desc", limit=limit)


@frappe.whitelist()
def run_trace(run_name: str):
    """Every naira in a run walks back to its earning events and rules."""
    run = frappe.get_doc("Payroll Run Event", run_name)
    out = []
    for l in run.lines:
        events = json.loads(l.earning_events_json or "[]")
        out.append({"employee": l.employee, "base": l.base_salary,
                    "earnings": frappe.get_all(
                        "Commission Earning Event",
                        filters={"name": ("in", events)},
                        fields=["name", "earning_type", "rule_version",
                                "amount", "source_reference"]) if events else [],
                    "net": l.net_pay})
    return {"run": run.name, "status": run.status,
            "consequence": run.get("consequence_name"), "lines": out}
