"""The ONE authoritative consequence writer for affiliate commission.

Mirrors Package 09 settlement EXACTLY, because we pay media buyers the same way we
pay delivery agents and suppliers — they are all accounts payable:

    Affiliate Commission Event  -> Purchase Invoice   (accrue: supplier owed)
    Affiliate Payout Event      -> Payment Entry      (settle: references the PI)

Why a Purchase Invoice and not a Journal Entry: the invoice IS the payable
document. It carries the Supplier party natively, appears in standard ERPNext AP
ageing and supplier statements, and lets the Payment Entry REFERENCE it so the
payment is matched rather than sitting on account. Every portal, report and agent
then reads "what do we owe this buyer" straight from ERPNext AP — recorded once,
never recalculated.

Every posting is idempotent on vv_source_event_key and linked back with
link_consequence(), which refuses to repoint an event at a different consequence.
"""
import frappe
from frappe.utils import flt, nowdate

from vitalvida.affiliate.config import (get_config, resolve_accounts,
                                        resolve_bank, resolve_commission_item,
                                        resolve_supplier)
from vitalvida.integration.consequence import link_consequence
from vitalvida.integration.idempotency import ensure_once, source_key


def post_commission_accrual(event_name):
    """Affiliate Commission Event -> Purchase Invoice. The buyer is now owed."""
    event = frappe.get_doc("Affiliate Commission Event", event_name)
    if event.purchase_invoice:
        return event.purchase_invoice
    acc = resolve_accounts()
    supplier = resolve_supplier(event.media_buyer)
    item = resolve_commission_item()
    amount = flt(event.commission_amount)
    if amount <= 0:
        frappe.throw(f"{event_name}: non-positive commission cannot post.")

    key = source_key("vv.affiliate.commission_earned", event.name)
    res = ensure_once("Purchase Invoice", {"vv_source_event_key": key}, lambda: {
        "doctype": "Purchase Invoice", "company": acc["company"],
        "supplier": supplier,
        "posting_date": event.earned_on or nowdate(), "set_posting_time": 1,
        "credit_to": acc["payable"],
        "vv_source_event_key": key,
        "remarks": f"Affiliate commission {event.name} "
                   f"(order {event.vv_order}, buyer {event.media_buyer})",
        "items": [{
            "item_code": item, "qty": 1, "rate": amount,
            "expense_account": acc["expense"], "cost_center": acc["cost_center"],
            "description": f"Affiliate commission for order {event.vv_order} "
                           f"(rule {event.commission_rule} v{event.rule_version})",
        }],
    })
    if res["created"]:
        doc = frappe.get_doc("Purchase Invoice", res["name"])
        if doc.docstatus == 0:
            doc.submit()
    link_consequence(event, "Purchase Invoice", res["name"])
    event.db_set("purchase_invoice", res["name"])
    return res["name"]


def post_payout_settlement(event_name):
    """Affiliate Payout Event -> Payment Entry that REFERENCES the invoices.

    Referencing is what clears the payable. An unreferenced ("on account")
    payment leaves the invoice open forever and AP ageing never resolves.
    """
    event = frappe.get_doc("Affiliate Payout Event", event_name)
    if event.payment_entry:
        return event.payment_entry
    acc = resolve_accounts()
    bank = resolve_bank()
    supplier = resolve_supplier(event.media_buyer)
    amount = flt(event.total_amount)
    if amount <= 0:
        frappe.throw(f"{event_name}: non-positive payout cannot post.")

    # every line must already be accrued on a submitted Purchase Invoice
    references, credit_to = [], None
    for line in event.lines:
        pi_name = frappe.db.get_value("Affiliate Commission Event",
                                      line.commission_event, "purchase_invoice")
        if not pi_name:
            frappe.throw(f"{line.commission_event} has no Purchase Invoice; "
                         "commission must be accrued before it is paid.")
        pi = frappe.get_doc("Purchase Invoice", pi_name)
        if pi.docstatus != 1:
            frappe.throw(f"Purchase Invoice {pi_name} is not submitted.")
        credit_to = credit_to or pi.credit_to
        references.append({"reference_doctype": "Purchase Invoice",
                           "reference_name": pi_name,
                           "allocated_amount": flt(line.amount)})

    key = source_key("vv.affiliate.payout_settled", event.name)
    res = ensure_once("Payment Entry", {"vv_source_event_key": key}, lambda: {
        "doctype": "Payment Entry", "payment_type": "Pay",
        "company": acc["company"], "posting_date": event.paid_on or nowdate(),
        "party_type": "Supplier", "party": supplier,
        "paid_from": bank, "paid_to": credit_to or acc["payable"],
        "paid_amount": amount, "received_amount": amount,
        "reference_no": event.external_reference, "reference_date": event.paid_on or nowdate(),
        "cost_center": acc["cost_center"], "vv_source_event_key": key,
        "references": references,
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
