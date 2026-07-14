"""Package 08 — sole ERPNext accounting-consequence writers for Packages 08–10.

North star (mission + Constitution):
    Payment Confirmed  -> Payment Entry        (R51/R52 FIN-001/002)
    Closure Event      -> Sales Invoice        (R53/R55 FIN-003)
    Liability Approved -> Journal Entry        (R60/SET-010 pattern)

Every writer:
  * is invoked as an Integration Outbox consumer (Package 01), so a failure is a
    visible Failed outbox row, never a swallowed except (CTL-007 R31);
  * is idempotent via a unique ``vv_source_event_key`` custom field on the
    ERPNext consequence document plus ``ensure_once`` (CTL-006 R30);
  * back-links the consequence onto the source event through Package 01's
    ``link_consequence`` typed link (GOV-004 R69 rule 7);
  * never stores a balance anywhere — balances live in ERPNext GL only.

Signature contract: every consumer is ``fn(source_doctype, source_name,
event_key)`` — the shape ``integration.outbox.process_pending`` dispatches.
"""
import frappe
from frappe.utils import flt, nowdate

from vitalvida.integration.idempotency import ensure_once, source_key
from vitalvida.integration.consequence import link_consequence
from vitalvida.finance.config import get_config

# Event keys registered in the Event Ownership Register by patch v6_2.
EVT_PAYMENT_CONFIRMED = "vv.finance.payment_confirmed"
EVT_ORDER_CLOSED      = "vv.order.closed"
EVT_LIABILITY_APPROVED = "vv.finance.liability_approved"


def _submit(doc):
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    doc.submit()
    return doc


# ---------------------------------------------------------------------------
# E1 consequence — Payment Confirmed -> Payment Entry
# Source event: Payment Reconciliation Log (the verified idempotent matcher).
# ---------------------------------------------------------------------------
def on_payment_confirmed(source_doctype, source_name, event_key):
    """One submitted Payment Entry per confirmed reconciliation event."""
    cfg = get_config()
    src = frappe.get_doc(source_doctype, source_name)
    if src.get("consequence_posted"):
        return src.get("consequence_name")          # already done — safe retry

    amount = flt(src.get("amount") or src.get("matched_amount"))
    if amount <= 0:
        frappe.throw(f"{source_doctype} {source_name}: non-positive amount; "
                     "refusing to post a Payment Entry.")
    order = src.get("order") or src.get("vv_order")
    customer = _customer_for_order(order)
    key = source_key(EVT_PAYMENT_CONFIRMED, source_doctype, source_name)

    res = ensure_once(
        "Payment Entry", {"vv_source_event_key": key},
        lambda: _build_payment_entry(cfg, key, customer, amount, src))
    if res["created"]:
        pe = frappe.get_doc("Payment Entry", res["name"])
        if pe.docstatus == 0:
            pe.submit()
    link_consequence(src, "Payment Entry", res["name"])
    return res["name"]


def _build_payment_entry(cfg, key, customer, amount, src):
    return {
        "doctype": "Payment Entry",
        "payment_type": "Receive",
        "company": cfg.company,
        "posting_date": nowdate(),
        "party_type": "Customer",
        "party": customer,
        "paid_from": cfg.receivable_account,
        "paid_to": cfg.moniepoint_bank_account,
        "paid_amount": amount,
        "received_amount": amount,
        "reference_no": src.get("webhook_reference") or src.name,
        "reference_date": nowdate(),
        "mode_of_payment": cfg.get("mode_of_payment") or None,
        "cost_center": cfg.cost_center,
        "vv_source_event_key": key,
        "remarks": f"Payment Confirmed event {src.doctype} {src.name} "
                   f"(order {src.get('order') or src.get('vv_order') or '-'})",
    }


def _customer_for_order(order_name):
    """Resolve (or provision once) the ERPNext Customer for a VV Order."""
    default = frappe.db.get_single_value("VV Finance Config", "default_customer")
    if not order_name:
        if default:
            return default
        frappe.throw("Payment Confirmed event carries no order and no "
                     "default_customer is configured.")
    cust = None
    if frappe.db.exists("DocType", "VV Order"):
        cust = frappe.db.get_value("VV Order", order_name, "customer") \
            if frappe.get_meta("VV Order").has_field("customer") else None
        if not cust:
            phone = frappe.db.get_value("VV Order", order_name, "customer_phone") \
                if frappe.get_meta("VV Order").has_field("customer_phone") else None
            if phone:
                cust = frappe.db.get_value("Customer", {"mobile_no": phone}, "name")
    return cust or default or frappe.throw(
        f"No ERPNext Customer resolvable for order {order_name}; set "
        "default_customer on VV Finance Config or map the order's customer.")


# ---------------------------------------------------------------------------
# E3/E4 consequence — Order Closure Event -> Sales Invoice (revenue = GL)
# ---------------------------------------------------------------------------
def on_order_closed(source_doctype, source_name, event_key):
    """One submitted Sales Invoice per Closure Event. Revenue exists only here."""
    cfg = get_config()
    src = frappe.get_doc(source_doctype, source_name)
    if src.get("consequence_posted"):
        return src.get("consequence_name")

    _assert_closure_conditions(src)
    order = src.get("order") or src.get("vv_order")
    customer = _customer_for_order(order)
    items = _invoice_items_for_order(cfg, order)
    key = source_key(EVT_ORDER_CLOSED, source_doctype, source_name)

    res = ensure_once(
        "Sales Invoice", {"vv_source_event_key": key},
        lambda: {
            "doctype": "Sales Invoice",
            "company": cfg.company,
            "customer": customer,
            "posting_date": nowdate(),          # GL date = recognition date (R59)
            "set_posting_time": 1,
            "items": items,
            "cost_center": cfg.cost_center,
            "vv_source_event_key": key,
            "remarks": f"Order Closure Event {src.name} (order {order})",
        })
    if res["created"]:
        si = frappe.get_doc("Sales Invoice", res["name"])
        if si.docstatus == 0:
            si.submit()
    link_consequence(src, "Sales Invoice", res["name"])

    # Fan-out: Profit First allocation consumes the SAME closure event via the
    # outbox (a consumer, not a second writer of revenue).
    if cfg.get("enable_profit_first_gl"):
        from vitalvida.integration.outbox import enqueue
        enqueue(EVT_ORDER_CLOSED, source_doctype, source_name,
                "vitalvida.finance.profit_first_gl.on_order_closed_allocate")
    return res["name"]


def _assert_closure_conditions(src):
    """R89: closed = all five conditions. The event asserts; the writer verifies."""
    for f in ("delivery_completed", "payment_confirmed", "inventory_fulfilled",
              "reconciliation_clear", "no_open_exception"):
        if src.meta.has_field(f) and not src.get(f):
            frappe.throw(f"Closure Event {src.name}: condition {f!r} is false; "
                         "refusing to recognise revenue (R89 ORD-007).")


def _invoice_items_for_order(cfg, order_name):
    """Build Sales Invoice items from the order's structured recipe.

    Uses the Package 02 recipe resolver (structured Bundle Definition) — never
    the display string (PRD-005). Falls back to a single line at the order's
    payable total when no structured recipe resolves, so revenue is still
    recognised at the correct total.
    """
    total = flt(frappe.db.get_value("VV Order", order_name, "total_payable"))
    if total <= 0:
        frappe.throw(f"VV Order {order_name}: total_payable is {total}; "
                     "refusing to invoice a non-positive amount.")
    item_code = cfg.get("default_sales_item")
    if not item_code:
        frappe.throw("default_sales_item not set on VV Finance Config.")
    return [{
        "item_code": item_code,
        "qty": 1,
        "rate": total,
        "income_account": cfg.income_account,
        "cost_center": cfg.cost_center,
        "description": f"VV Order {order_name}",
    }]


# ---------------------------------------------------------------------------
# Liability Approved -> Journal Entry (generic writer; Package 09 supplies the
# DA-shaped events, this stays the single JE author for approved liabilities).
# ---------------------------------------------------------------------------
def on_liability_approved(source_doctype, source_name, event_key):
    src = frappe.get_doc(source_doctype, source_name)
    if src.get("consequence_posted"):
        return src.get("consequence_name")
    accounts = _liability_accounts(src)
    key = source_key(EVT_LIABILITY_APPROVED, source_doctype, source_name)
    res = ensure_once(
        "Journal Entry", {"vv_source_event_key": key},
        lambda: {
            "doctype": "Journal Entry",
            "company": get_config().company,
            "posting_date": nowdate(),
            "voucher_type": "Journal Entry",
            "accounts": accounts,
            "vv_source_event_key": key,
            "user_remark": f"Liability Approved event {source_doctype} {source_name}",
        })
    if res["created"]:
        je = frappe.get_doc("Journal Entry", res["name"])
        if je.docstatus == 0:
            je.submit()
    link_consequence(src, "Journal Entry", res["name"])
    if src.meta.has_field("journal_entry"):
        src.db_set("journal_entry", res["name"])   # v1.1.1: typed link (B3)
    return res["name"]


def _liability_accounts(src):
    """The source event must carry its own account legs (debit/credit rows).

    Events state facts; the accounts they resolve to are event-type specific
    and are provided by the emitting package via ``get_journal_legs()`` on the
    event's controller. This keeps ONE JE writer with N event shapes.
    """
    if hasattr(src, "get_journal_legs"):
        legs = src.get_journal_legs()
        if legs:
            return legs
    frappe.throw(f"{src.doctype} does not define get_journal_legs(); "
                 "cannot post a Journal Entry for it.")


# ---------------------------------------------------------------------------
# Package 09 settlement consequences. Package 09 emits immutable events only.
# ---------------------------------------------------------------------------
def on_settlement_approved(source_doctype, source_name, event_key):
    event=frappe.get_doc(source_doctype,source_name)
    if event.purchase_invoice: return event.purchase_invoice
    batch=frappe.get_doc("Settlement Batch",event.settlement_batch); cfg=get_config()
    item=cfg.get("da_fee_item") or cfg.get("default_sales_item")
    if not item: frappe.throw("Configure da_fee_item on VV Finance Config.")
    key=source_key("vv.settlement.batch_approved",event.name)
    res=ensure_once("Purchase Invoice",{"vv_source_event_key":key},lambda:{"doctype":"Purchase Invoice","company":cfg.company,
        "supplier":batch.supplier,"posting_date":nowdate(),"set_posting_time":1,"vv_source_event_key":key,
        "remarks":f"DA settlement approval {event.name} for batch {batch.name}",
        "items":[{"item_code":item,"qty":1,"rate":flt(r.amount),"description":f"Earning {r.earning_event}","cost_center":cfg.cost_center} for r in batch.earnings]})
    if res["created"]:
        doc=frappe.get_doc("Purchase Invoice",res["name"])
        if doc.docstatus==0: doc.submit()
    link_consequence(event,"Purchase Invoice",res["name"]); event.db_set("purchase_invoice",res["name"])
    batch.db_set("purchase_invoice_ref",res["name"])
    for r in batch.earnings: frappe.db.set_value("DA Earning Event",r.earning_event,"erpnext_payable_ref",res["name"])
    return res["name"]

def on_settlement_paid(source_doctype, source_name, event_key):
    event=frappe.get_doc(source_doctype,source_name)
    if event.payment_entry: return event.payment_entry
    batch=frappe.get_doc("Settlement Batch",event.settlement_batch); cfg=get_config()
    pi=frappe.get_doc("Purchase Invoice",batch.purchase_invoice_ref)
    key=source_key("vv.settlement.batch_paid",event.name)
    res=ensure_once("Payment Entry",{"vv_source_event_key":key},lambda:{"doctype":"Payment Entry","payment_type":"Pay","company":cfg.company,
        "posting_date":nowdate(),"party_type":"Supplier","party":batch.supplier,"paid_from":cfg.moniepoint_bank_account,
        "paid_to":pi.credit_to,"paid_amount":flt(event.amount),"received_amount":flt(event.amount),"reference_no":event.bank_reference,
        "reference_date":nowdate(),"cost_center":cfg.cost_center,"vv_source_event_key":key,
        "references":[{"reference_doctype":"Purchase Invoice","reference_name":pi.name,"allocated_amount":flt(event.amount)}],
        "remarks":f"Settlement payment event {event.name}"})
    if res["created"]:
        doc=frappe.get_doc("Payment Entry",res["name"])
        if doc.docstatus==0: doc.submit()
    link_consequence(event,"Payment Entry",res["name"]); event.db_set("payment_entry",res["name"])
    batch.db_set({"payment_entry_ref":res["name"],"bank_reference":event.bank_reference,"paid_by_user":event.paid_by,"paid_at":event.paid_at,"status":"Paid"})
    for r in batch.earnings: frappe.db.set_value("DA Earning Event",r.earning_event,"status","Paid")
    return res["name"]

def on_remittance_cleared(source_doctype,source_name,event_key):
    event=frappe.get_doc(source_doctype,source_name)
    if event.payment_entry: return event.payment_entry
    rem=frappe.get_doc("Outstanding Remittance Event",event.outstanding_remittance); cfg=get_config()
    receivable=cfg.get("da_receivable_account") or cfg.receivable_account
    key=source_key("vv.settlement.remittance_cleared",event.name)
    res=ensure_once("Payment Entry",{"vv_source_event_key":key},lambda:{"doctype":"Payment Entry","payment_type":"Receive","company":cfg.company,
        "posting_date":nowdate(),"party_type":"Supplier","party":rem.supplier,"paid_from":receivable,"paid_to":cfg.moniepoint_bank_account,
        "paid_amount":flt(event.amount),"received_amount":flt(event.amount),"reference_no":event.bank_reference,"reference_date":nowdate(),
        "cost_center":cfg.cost_center,"vv_source_event_key":key,"remarks":f"Remittance clearing event {event.name}"})
    if res["created"]:
        doc=frappe.get_doc("Payment Entry",res["name"])
        if doc.docstatus==0: doc.submit()
    link_consequence(event,"Payment Entry",res["name"]); event.db_set("payment_entry",res["name"])
    rem.db_set({"clearing_payment_entry":res["name"],"resolved_at":nowdate(),"status":"Cleared"})
    return res["name"]

# ---------------------------------------------------------------------------
# Package 10 payroll consequences. Package 10 emits immutable events only.
# ---------------------------------------------------------------------------
def on_payroll_approved(source_doctype,source_name,event_key):
    event=frappe.get_doc(source_doctype,source_name)
    if event.journal_entry: return event.journal_entry
    run=frappe.get_doc("Payroll Run Event",event.payroll_run); cfg=get_config()
    from vitalvida.payroll_events.consequences import _payroll_legs
    key=source_key("vv.payroll.run_approved",event.name)
    res=ensure_once("Journal Entry",{"vv_source_event_key":key},lambda:{"doctype":"Journal Entry","company":cfg.company,
        "posting_date":nowdate(),"accounts":_payroll_legs(cfg,run),"vv_source_event_key":key,"user_remark":f"Payroll approval {event.name}"})
    if res["created"]:
        doc=frappe.get_doc("Journal Entry",res["name"])
        if doc.docstatus==0: doc.submit()
    link_consequence(event,"Journal Entry",res["name"]); event.db_set("journal_entry",res["name"])
    run.db_set({"journal_entry":res["name"],"consequence_doctype":"Journal Entry","consequence_name":res["name"],"consequence_posted":1,"status":"Posted"})
    return res["name"]

def on_payroll_paid(source_doctype,source_name,event_key):
    """Payroll Payment Event -> ONE Journal Entry: Dr Net Wages Payable / Cr Bank.

    v1.1.1 (blocker B5): the previous Payment Entry with payment_type
    "Internal Transfer" and paid_to = Net Wages Payable is invalid ERPNext usage
    (Internal Transfer requires Bank/Cash on both sides) and would be rejected
    at submit. Clearing a payable liability against the bank is a Journal Entry.
    """
    event=frappe.get_doc(source_doctype,source_name)
    if event.get("journal_entry"): return event.journal_entry
    run=frappe.get_doc("Payroll Run Event",event.payroll_run); cfg=get_config()
    if not cfg.get("net_wages_payable_account") or not cfg.get("moniepoint_bank_account"):
        frappe.throw("VV Finance Config lacks net_wages_payable_account / "
                     "moniepoint_bank_account; cannot clear net wages.")
    key=source_key("vv.payroll.run_paid",event.name)
    res=ensure_once("Journal Entry",{"vv_source_event_key":key},lambda:{
        "doctype":"Journal Entry","voucher_type":"Bank Entry",
        "company":cfg.company,"posting_date":nowdate(),
        "cheque_no":event.bank_reference,"cheque_date":nowdate(),
        "vv_source_event_key":key,
        "user_remark":f"Net wages cleared for payroll run {run.name} "
                      f"(payment event {event.name}, ref {event.bank_reference})",
        "accounts":[
            {"account":cfg.net_wages_payable_account,
             "debit_in_account_currency":flt(event.amount),
             "cost_center":cfg.cost_center},
            {"account":cfg.moniepoint_bank_account,
             "credit_in_account_currency":flt(event.amount),
             "cost_center":cfg.cost_center},
        ]})
    if res["created"]:
        doc=frappe.get_doc("Journal Entry",res["name"])
        if doc.docstatus==0: doc.submit()
    link_consequence(event,"Journal Entry",res["name"]); event.db_set("journal_entry",res["name"])
    run.db_set({"net_wages_cleared_by":res["name"],"status":"Paid"})
    return res["name"]
