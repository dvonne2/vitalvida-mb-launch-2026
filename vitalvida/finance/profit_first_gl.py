"""Profit First on the GL (FIN-013 R63) — retires the mutable bucket balance.

Old world (`profit_first.py`): two writers mutate
``Profit First Bucket.current_balance`` with non-atomic ``set_value`` — a
parallel ledger. New world: allocations are Journal Entries between the source
account and per-bucket GL accounts; a bucket "balance" is *derived* via
``get_balance_on`` and stored nowhere.
"""
import frappe
from frappe.utils import flt, nowdate

from vitalvida.integration.idempotency import ensure_once, source_key
from vitalvida.finance.config import get_config

EVT_ALLOCATED = "vv.finance.profit_first_allocated"

BUCKET_FIELD = {
    "Owner Pay":   "pf_owner_pay_account",
    "Tax Reserve": "pf_tax_reserve_account",
    "Profit":      "pf_profit_account",
    "OpEx":        "pf_opex_account",
}


def _percentages(cfg):
    out = {}
    for bucket in BUCKET_FIELD:
        pct = flt(cfg.get("pf_pct_" + bucket.lower().replace(" ", "_")))
        if pct:
            out[bucket] = pct
    total = sum(out.values())
    if not out or total > 100.0001:
        frappe.throw(f"Profit First percentages invalid (sum={total}); fix "
                     "VV Finance Config before allocating (R63).")
    return out


def on_order_closed_allocate(source_doctype, source_name, event_key):
    """Outbox consumer: one allocation JE per closure event's recognised amount."""
    cfg = get_config(require_pf=True)
    if not cfg.get("enable_profit_first_gl"):
        return
    src = frappe.get_doc(source_doctype, source_name)
    si_name = src.get("consequence_name")
    if not si_name or src.get("consequence_doctype") != "Sales Invoice":
        frappe.throw(f"{source_name}: allocation requires the Sales Invoice "
                     "consequence to exist first (consume, don't recompute).")
    amount = flt(frappe.db.get_value("Sales Invoice", si_name, "base_grand_total"))
    key = source_key(EVT_ALLOCATED, source_doctype, source_name)

    legs = [{"account": cfg.pf_source_account,
             "credit_in_account_currency": amount,
             "cost_center": cfg.cost_center}]
    for bucket, pct in _percentages(cfg).items():
        share = round(amount * pct / 100.0, 2)
        if share:
            legs.append({"account": cfg.get(BUCKET_FIELD[bucket]),
                         "debit_in_account_currency": share,
                         "cost_center": cfg.cost_center})
    # rounding drift goes to OpEx so the JE balances to the credit exactly
    drift = round(amount - sum(flt(l.get("debit_in_account_currency")) for l in legs[1:]), 2)
    if drift:
        legs.append({"account": cfg.pf_opex_account,
                     "debit_in_account_currency": drift,
                     "cost_center": cfg.cost_center})

    res = ensure_once(
        "Journal Entry", {"vv_source_event_key": key},
        lambda: {"doctype": "Journal Entry", "company": cfg.company,
                 "posting_date": nowdate(), "accounts": legs,
                 "vv_source_event_key": key,
                 "user_remark": f"Profit First allocation for {si_name} "
                                f"(closure {source_name})"})
    if res["created"]:
        je = frappe.get_doc("Journal Entry", res["name"])
        if je.docstatus == 0:
            je.submit()
    return res["name"]


@frappe.whitelist()
def bucket_balances(as_on=None):
    """Derived, never stored (R63). The ONLY sanctioned bucket-balance read."""
    from erpnext.accounts.utils import get_balance_on
    cfg = get_config(require_pf=True)
    as_on = as_on or nowdate()
    return {bucket: flt(get_balance_on(cfg.get(field), date=as_on,
                                       company=cfg.company))
            for bucket, field in BUCKET_FIELD.items()}
