"""Package 16 — Profit First allocations on the General Ledger.

Cash is allocated only after a Payment Confirmed event has produced a submitted
Payment Entry. Revenue recognition remains owned by the Order Closed event.
No mutable wallet or bucket balance is maintained: every balance is derived
from ERPNext GL entries.
"""
import frappe
from frappe.utils import flt, nowdate
from vitalvida.integration.idempotency import ensure_once, source_key
from vitalvida.finance.config import get_config

EVT_ALLOCATED = "vv.finance.profit_first_allocated"
BUCKET_FIELD = {
    "Owner Pay": "pf_owner_pay_account",
    "Tax Reserve": "pf_tax_reserve_account",
    "Profit": "pf_profit_account",
    "Operating Expenses": "pf_opex_account",
    "Growth Reserve": "pf_growth_account",
    "Payroll Reserve": "pf_payroll_account",
    "Refund Reserve": "pf_refund_account",
}


def _percentages(cfg):
    out={}
    for bucket,field in BUCKET_FIELD.items():
        pct=flt(cfg.get("pf_pct_" + field.removeprefix("pf_").removesuffix("_account")))
        if pct < 0: frappe.throw(f"Profit First percentage cannot be negative: {bucket}")
        if pct: out[bucket]=pct
    total=round(sum(out.values()),6)
    if not out or abs(total-100.0) > 0.0001:
        frappe.throw(f"Profit First percentages must total exactly 100%; current total={total}.")
    return out


def _payment_entry_for(src):
    if src.get("consequence_doctype") == "Payment Entry" and src.get("consequence_name"):
        return frappe.get_doc("Payment Entry", src.consequence_name)
    key=source_key("vv.finance.payment_confirmed", src.doctype, src.name)
    name=frappe.db.get_value("Payment Entry", {"vv_source_event_key":key}, "name")
    if not name: frappe.throw(f"{src.name}: submitted Payment Entry consequence must exist before Profit First allocation.")
    return frappe.get_doc("Payment Entry", name)


def on_payment_confirmed_allocate(source_doctype, source_name, event_key):
    cfg=get_config(require_pf=True)
    if not cfg.get("enable_profit_first_gl"): return
    src=frappe.get_doc(source_doctype, source_name)
    pe=_payment_entry_for(src)
    if pe.docstatus != 1: frappe.throw(f"Payment Entry {pe.name} is not submitted.")
    amount=flt(pe.received_amount or pe.paid_amount)
    if amount <= 0: frappe.throw(f"Payment Entry {pe.name} has no positive received amount.")
    percentages=_percentages(cfg)
    if cfg.get("profit_first_mode") != "Active":
        return {"mode":"Shadow","payment_entry":pe.name,"amount":amount,
                "proposed":[{"bucket":b,"percentage":p,"amount":round(amount*p/100,2)} for b,p in percentages.items()]}
    key=source_key(EVT_ALLOCATED, source_doctype, source_name)
    legs=[{"account":cfg.pf_source_account,"credit_in_account_currency":amount,"cost_center":cfg.cost_center}]
    debited=0.0
    items=list(percentages.items())
    for index,(bucket,pct) in enumerate(items):
        share=round(amount-debited,2) if index==len(items)-1 else round(amount*pct/100.0,2)
        debited += share
        legs.append({"account":cfg.get(BUCKET_FIELD[bucket]),"debit_in_account_currency":share,"cost_center":cfg.cost_center})
    res=ensure_once("Journal Entry", {"vv_source_event_key":key}, lambda:{
        "doctype":"Journal Entry","voucher_type":"Bank Entry","company":cfg.company,
        "posting_date":nowdate(),"accounts":legs,"vv_source_event_key":key,
        "user_remark":f"Profit First cash allocation for confirmed Payment Entry {pe.name}"})
    if res["created"]:
        je=frappe.get_doc("Journal Entry",res["name"])
        if je.docstatus==0: je.submit()
    return {"mode":"Active","journal_entry":res["name"],"payment_entry":pe.name,"amount":amount}


# compatibility: legacy event method now refuses to allocate on closure
def on_order_closed_allocate(source_doctype, source_name, event_key):
    frappe.throw("Profit First allocation moved to Payment Confirmed in Package 16; closure-based allocation is prohibited.")


@frappe.whitelist()
def bucket_balances(as_on=None):
    from erpnext.accounts.utils import get_balance_on
    cfg=get_config(require_pf=True); as_on=as_on or nowdate()
    return {bucket:flt(get_balance_on(cfg.get(field),date=as_on,company=cfg.company))
            for bucket,field in BUCKET_FIELD.items() if cfg.get(field)}
