"""The ONE authoritative consequence writer for affiliate commission.

Mirrors Package 10 payroll exactly:
    Affiliate Commission Event  -> Journal Entry   (accrue expense + payable)
    Affiliate Payout Event      -> Payment Entry   (settle the payable)

Package 17 owns these two writers and nothing else. Package 08 remains owner of
generic order/payment/liability consequences; Package 10 owns payroll.

Every posting is idempotent on vv_source_event_key and linked back with
link_consequence(), which refuses to repoint an event at a different consequence
— the anti-double-post guard.
"""
import frappe
from frappe.utils import flt, nowdate

from vitalvida.affiliate.config import resolve_accounts, resolve_bank, get_config
from vitalvida.integration.consequence import link_consequence
from vitalvida.integration.idempotency import ensure_once, source_key


def post_commission_accrual(event_name):
    """Affiliate Commission Event -> Journal Entry. Dr Expense, Cr Payable."""
    event = frappe.get_doc("Affiliate Commission Event", event_name)
    if event.journal_entry:
        return event.journal_entry
    acc = resolve_accounts()
    amount = flt(event.commission_amount)
    if amount <= 0:
        frappe.throw(f"{event_name}: non-positive commission cannot post.")

    key = source_key("vv.affiliate.commission_earned", event.name)
    legs = [
        {"account": acc["expense"], "debit_in_account_currency": amount,
         "cost_center": acc["cost_center"]},
        {"account": acc["payable"], "credit_in_account_currency": amount,
         "cost_center": acc["cost_center"]},
    ]
    res = ensure_once("Journal Entry", {"vv_source_event_key": key}, lambda: {
        "doctype": "Journal Entry", "company": acc["company"],
        "posting_date": event.earned_on or nowdate(), "accounts": legs,
        "vv_source_event_key": key,
        "user_remark": f"Affiliate commission earned {event.name} "
                       f"(order {event.vv_order}, buyer {event.media_buyer})",
    })
    if res["created"]:
        doc = frappe.get_doc("Journal Entry", res["name"])
        if doc.docstatus == 0:
            doc.submit()
    link_consequence(event, "Journal Entry", res["name"])
    event.db_set("journal_entry", res["name"])
    return res["name"]


def post_payout_settlement(event_name):
    """Affiliate Payout Event -> Payment Entry. Dr Payable, Cr Bank."""
    event = frappe.get_doc("Affiliate Payout Event", event_name)
    if event.payment_entry:
        return event.payment_entry
    acc = resolve_accounts()
    bank = resolve_bank()
    amount = flt(event.total_amount)
    if amount <= 0:
        frappe.throw(f"{event_name}: non-positive payout cannot post.")

    key = source_key("vv.affiliate.payout_settled", event.name)
    res = ensure_once("Payment Entry", {"vv_source_event_key": key}, lambda: {
        "doctype": "Payment Entry", "payment_type": "Pay",
        "company": acc["company"], "posting_date": event.paid_on or nowdate(),
        "paid_from": bank, "paid_to": acc["payable"],
        "paid_amount": amount, "received_amount": amount,
        "reference_no": event.external_reference or event.name,
        "reference_date": event.paid_on or nowdate(),
        "vv_source_event_key": key,
        "remarks": f"Affiliate payout {event.name} "
                   f"(batch {event.payout_batch}, buyer {event.media_buyer})",
    })
    if res["created"]:
        doc = frappe.get_doc("Payment Entry", res["name"])
        if doc.docstatus == 0:
            doc.submit()
    link_consequence(event, "Payment Entry", res["name"])
    event.db_set("payment_entry", res["name"])
    return res["name"]
