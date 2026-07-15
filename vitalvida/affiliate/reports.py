"""Read-only affiliate projections.

AUTHORITY SPLIT — each question has exactly one answer, read from one place:

    "what was earned on order X?"   -> Affiliate Commission Event  (the event)
    "how much do we owe buyer Y?"   -> ERPNext GL party balance    (the ledger)
    "which orders are unpaid?"      -> events with no payout line

`outstanding_payable` reads the ERPNext party ledger — the same mechanism
Package 09 uses for delivery agents (`settlement.read_models.da_payable`). It is
NOT summed from events. That is the point: the amount owed is recorded once, by
the Purchase Invoice, and is immediately available to every portal, report and
agent without being recalculated anywhere.

`reconcile_payable` exists to prove the two agree. If they ever diverge, a
consequence is missing — that is a real defect, not a rounding difference.
"""
import frappe
from frappe.utils import flt, nowdate


def outstanding_payable(media_buyer, as_on=None):
    """What we owe this media buyer, straight from the ERPNext party ledger."""
    from erpnext.accounts.utils import get_balance_on
    from vitalvida.affiliate.config import resolve_accounts, resolve_supplier
    acc = resolve_accounts()
    supplier = resolve_supplier(media_buyer)
    as_on = as_on or nowdate()
    return {
        "metric": "affiliate_payable",
        "source": "ERPNext GL party balance",
        "media_buyer": media_buyer,
        "party": supplier,
        "as_on": as_on,
        "amount": flt(get_balance_on(acc["payable"], date=as_on,
                                     party_type="Supplier", party=supplier,
                                     company=acc["company"])),
    }


def unpaid_commission_events(media_buyer=None):
    """WHICH orders are unpaid. Derived from events; never a cached flag."""
    cond = "AND e.media_buyer = %(buyer)s" if media_buyer else ""
    return frappe.db.sql(f"""
        SELECT e.name, e.media_buyer, e.vv_order, e.commission_amount, e.earned_on
          FROM `tabAffiliate Commission Event` e
         WHERE e.purchase_invoice IS NOT NULL AND e.purchase_invoice != ''
           AND NOT EXISTS (SELECT 1 FROM `tabAffiliate Payout Line` l
                            WHERE l.commission_event = e.name)
           {cond}
         ORDER BY e.earned_on, e.name
    """, {"buyer": media_buyer}, as_dict=True)


def reconcile_payable(media_buyer, as_on=None):
    """CONTROL: the party ledger must equal the unpaid accrued events.

    They are two views of one fact. A divergence means a consequence is missing
    or an entry was posted outside the writers.
    """
    ledger = outstanding_payable(media_buyer, as_on)
    events = unpaid_commission_events(media_buyer)
    event_total = flt(sum(flt(e.commission_amount) for e in events))
    diff = round(flt(ledger["amount"]) - event_total, 2)
    return {"media_buyer": media_buyer, "as_on": ledger["as_on"],
            "ledger_amount": ledger["amount"], "unpaid_event_total": event_total,
            "unpaid_event_count": len(events), "difference": diff,
            "reconciled": diff == 0}


# ---- controls: these should always be empty ----

def unaccrued_commission():
    """Commission events not yet accrued. Always empty in a healthy system."""
    return frappe.get_all("Affiliate Commission Event",
                          filters={"purchase_invoice": ["in", [None, ""]]},
                          fields=["name", "vv_order", "media_buyer",
                                  "commission_amount", "earned_on"])


def orders_paid_without_event():
    """Legacy rows marked Paid with no authoritative payout event.

    Non-zero means money moved outside the ledger.
    """
    return frappe.db.sql("""
        SELECT o.name AS vv_order, o.media_buyer, o.affiliate_commission_amount
          FROM `tabVV Order` o
         WHERE o.affiliate_payout_status = 'Paid'
           AND NOT EXISTS (SELECT 1 FROM `tabAffiliate Payout Line` l
                            WHERE l.vv_order = o.name)
    """, as_dict=True)


def media_buyers_without_supplier():
    """CONTROL: a media buyer with no Supplier party cannot be paid at all."""
    return frappe.db.sql("""
        SELECT name, media_buyer_name FROM `tabVV Media Buyer`
         WHERE supplier IS NULL OR supplier = ''
    """, as_dict=True)
