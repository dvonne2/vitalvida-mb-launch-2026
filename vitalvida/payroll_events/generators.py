"""Package 10 — earning generators. Earnings are EVENTS created by CONSUMING
events; nothing here SUMs orders or COUNTs statuses.

    Telesales commission   consumes vv.order.closed (one earning per closure)
    Champion bonuses       bridges the EXISTING single-seam Bonus Approval
                           Request layer (Master v1.1: Refactor, not Build —
                           this bridge preserves the one writer; it does not
                           create a second bonus authority)
    DPSR/Revival/Recovery  consume their qualification events when the KPI
                           packages exist (generic qualification consumer)

Every earning stamps the Incentive Rule Version in force (R102) and snapshots
its inputs for reproducibility.
"""
import json

import frappe
from frappe.utils import flt, nowdate, now_datetime, getdate

from vitalvida.integration.idempotency import ensure_once, source_key


def _resolve_rule(rule_key, on_date=None):
    from vitalvida.vitalvida.doctype.incentive_rule_version.incentive_rule_version import resolve
    return resolve(rule_key, on_date=on_date)


def _period_of(date_str=None):
    d = getdate(date_str or nowdate())
    return d.strftime("%Y-%m")


def _mk_earning(employee, source_doctype, source_reference, earning_type,
                rule, amount, inputs, period):
    key = source_key(source_reference, earning_type, rule.name)
    return ensure_once(
        "Commission Earning Event", {"idempotency_key": key},
        {"doctype": "Commission Earning Event", "employee": employee,
         "source_doctype": source_doctype, "source_reference": source_reference,
         "earning_type": earning_type, "rule_version": rule.name,
         "inputs_json": json.dumps(inputs, default=str),
         "amount": flt(amount), "period": period, "status": "Earned",
         "earned_at": now_datetime(), "idempotency_key": key})


# ---------------------------------------------------------------------------
# Telesales — consumes vv.order.closed (registered via Event Consumer Map)
# ---------------------------------------------------------------------------
def emit_telesales_commission(source_doctype, source_name, event_key):
    """One earning per closure event, at the rule in force. Replaces
    payroll.py's month-end COUNT/SUM(Paid) recompute (Gap P1)."""
    src = frappe.get_doc(source_doctype, source_name)
    order = src.get("order") or src.get("vv_order")
    rep = _telesales_rep(order)
    if not rep:
        return None                       # no rep on the order: nothing earned
    employee = frappe.db.get_value("VV Employee", {"linked_closer": rep}, "name")
    if not employee:
        return None
    rule = _resolve_rule("telesales_commission")
    if rule.rule_type == "Flat Amount":
        amount = flt(rule.amount)
        inputs = {"order": order, "basis": "per closed order"}
    elif rule.rule_type == "Percentage":
        total = flt(frappe.db.get_value("VV Order", order, "total_payable"))
        amount = round(total * flt(rule.percentage) / 100.0, 2)
        inputs = {"order": order, "order_total": total,
                  "pct": rule.percentage}
    else:
        frappe.throw(f"Rule {rule.name}: parameterised telesales rules need a "
                     "bespoke resolver.")
    res = _mk_earning(employee, source_doctype, source_name, "Telesales",
                      rule, amount, inputs, _period_of())
    return res["name"]


def _telesales_rep(order):
    if order and frappe.db.exists("DocType", "VV Order") and \
       frappe.get_meta("VV Order").has_field("telesales_rep"):
        return frappe.db.get_value("VV Order", order, "telesales_rep")
    return None


# ---------------------------------------------------------------------------
# Champion bonuses — bridge the existing single seam (Bonus Approval Request)
# ---------------------------------------------------------------------------
def bridge_approved_bonuses(limit=200):
    """Convert Approved, unpaid, unvoided Bonus Approval Requests into
    Commission Earning Events (status Approved — the BAR carries its own
    approval). Idempotent per BAR; the BAR remains the bonus-emission
    authority; this bridge only carries approved facts into the payroll spine.
    Scheduled hourly; also called before a payroll run computes.
    """
    if not frappe.db.exists("DocType", "Bonus Approval Request"):
        return []
    rule = _resolve_rule("champion_bonus_passthrough")
    meta = frappe.get_meta("Bonus Approval Request")
    has_adj = meta.has_field("adjusted_amount")
    rows = frappe.get_all(
        "Bonus Approval Request",
        filters={"status": "Approved",
                 "l5_voided": ("!=", 1), "l5_paid": ("!=", 1)},
        fields=["name", "employee", "bonus_amount", "champion_type"]
               + (["adjusted_amount"] if has_adj else []),
        limit=limit)
    created = []
    for r in rows:
        amount = flt(r.get("adjusted_amount") or 0) or flt(r.bonus_amount)
        if not amount or not r.employee:
            continue
        res = _mk_earning(r.employee, "Bonus Approval Request", r.name,
                          "Champion Bonus", rule, amount,
                          {"bar": r.name, "champion_type": r.champion_type,
                           "bonus_amount": r.bonus_amount,
                           "adjusted_amount": r.get("adjusted_amount")},
                          _period_of())
        if res["created"]:
            frappe.db.set_value("Commission Earning Event", res["name"],
                                "status", "Approved")
            created.append(res["name"])
    return created


# ---------------------------------------------------------------------------
# Generic qualification consumer (DPSR / Revival / Recovery / Attribution)
# ---------------------------------------------------------------------------
QUALIFICATION_RULES = {
    "vv.kpi.dpsr_qualified":     ("DPSR",     "dpsr_bonus"),
    "vv.kpi.revival_qualified":  ("Revival",  "revival_bonus"),
    "vv.kpi.recovery_qualified": ("Recovery", "recovery_bonus"),
}


def emit_qualification_earning(source_doctype, source_name, event_key):
    """Consumes rule-versioned KPI qualification events (E20–E22) when those
    packages ship. The qualification event must carry employee + amount basis."""
    mapping = QUALIFICATION_RULES.get(event_key)
    if not mapping:
        frappe.throw(f"No earning mapping for {event_key}")
    earning_type, rule_key = mapping
    src = frappe.get_doc(source_doctype, source_name)
    employee = src.get("employee")
    if not employee:
        frappe.throw(f"{source_name}: qualification event carries no employee.")
    rule = _resolve_rule(rule_key)
    if rule.rule_type != "Flat Amount":
        frappe.throw(f"{rule_key}: only flat qualification bonuses supported; "
                     "extend deliberately, don't guess.")
    res = _mk_earning(employee, source_doctype, source_name, earning_type,
                      rule, flt(rule.amount),
                      {"qualification": source_name}, _period_of())
    return res["name"]


# ---------------------------------------------------------------------------
# Reversal (R101 PAY-010)
# ---------------------------------------------------------------------------
@frappe.whitelist()
def reverse_earning(earning_name: str, reason: str):
    orig = frappe.get_doc("Commission Earning Event", earning_name)
    key = source_key("reversal", orig.idempotency_key)
    res = ensure_once(
        "Commission Earning Event", {"idempotency_key": key},
        {"doctype": "Commission Earning Event", "employee": orig.employee,
         "source_doctype": orig.doctype, "source_reference": orig.name,
         "earning_type": orig.earning_type, "rule_version": orig.rule_version,
         "inputs_json": json.dumps({"reversal_reason": reason}),
         "amount": -flt(orig.amount), "period": _period_of(),
         "status": "Earned", "earned_at": now_datetime(),
         "idempotency_key": key, "reversal_of": orig.name})
    if res["created"] and orig.status in ("Earned", "Approved"):
        orig.db_set("status", "Reversed")
    return res["name"]
