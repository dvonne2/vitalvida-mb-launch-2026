"""Affiliate payout — the one writer for "commission was paid".

A payout settles commission that has ALREADY been earned and accrued. It can
only pay Affiliate Commission Events that carry a posted Journal Entry, so the
payable being settled provably exists. Paying an un-accrued commission is
refused: that is exactly how money moves without a ledger entry.

Separation of duties: the approver may not be the payer.
"""
import frappe
from frappe.utils import flt, now_datetime, nowdate

from vitalvida.governance.hashing import stable_hash
from vitalvida.governance.immutable import require_distinct_users
from vitalvida.integration.idempotency import ensure_once, source_key

SERVICE = "vitalvida.affiliate.payout"


def payable_commission(media_buyer, period_start, period_end):
    """DERIVED: earned, accrued, not yet paid. Never a cached field."""
    return frappe.db.sql("""
        SELECT e.name, e.vv_order, e.commission_amount
          FROM `tabAffiliate Commission Event` e
         WHERE e.media_buyer = %(buyer)s
           AND e.earned_on BETWEEN %(start)s AND %(end)s
           AND e.journal_entry IS NOT NULL AND e.journal_entry != ''
           AND NOT EXISTS (SELECT 1 FROM `tabAffiliate Payout Line` l
                            WHERE l.commission_event = e.name)
         ORDER BY e.earned_on, e.name
    """, {"buyer": media_buyer, "start": period_start, "end": period_end}, as_dict=True)


def record_payout(payout_batch, external_reference, approved_by=None):
    """Record the payout fact and post its Payment Entry. Idempotent.

    Refuses unless every line is an accrued commission event. The Payment Entry
    is the authoritative consequence; without it, no payout is recorded.
    """
    if not external_reference:
        frappe.throw("An external payment reference is required to record a payout.")
    batch = frappe.get_doc("Affiliate Payout Batch", payout_batch)
    if batch.status != "Approved":
        frappe.throw(f"Batch {payout_batch} is {batch.status}; only Approved "
                     "batches can be paid.")
    approver = approved_by or batch.get("approved_by")
    require_distinct_users(approver, frappe.session.user, "pay")

    lines = payable_commission(batch.media_buyer, batch.period_start, batch.period_end)
    if not lines:
        frappe.throw(
            f"No accrued, unpaid commission for {batch.media_buyer} in "
            f"{batch.period_start}..{batch.period_end}. Commission must be earned "
            "and accrued (Journal Entry posted) before it can be paid.")
    total = flt(sum(flt(l.commission_amount) for l in lines))
    lines_hash = stable_hash([l.name for l in lines])
    key = source_key("AFFPAY", payout_batch, lines_hash)

    res = ensure_once("Affiliate Payout Event", {"source_key": key}, lambda: {
        "source_key": key, "payout_batch": payout_batch,
        "media_buyer": batch.media_buyer,
        "period_start": batch.period_start, "period_end": batch.period_end,
        "total_amount": total, "line_count": len(lines),
        "lines_hash": lines_hash, "currency": "NGN",
        "external_reference": external_reference,
        "paid_on": nowdate(), "paid_at": now_datetime(),
        "paid_by": frappe.session.user, "approved_by": approver,
        "recorded_by_service": SERVICE,
        "lines": [{"commission_event": l.name, "vv_order": l.vv_order,
                   "amount": flt(l.commission_amount)} for l in lines],
    })
    if res["created"]:
        from vitalvida.affiliate.consequences import post_payout_settlement
        post_payout_settlement(res["name"])
        _project_to_orders(res["name"], payout_batch)
    return res["name"]


def _project_to_orders(event_name, payout_batch):
    """Legacy PROJECTION only. The payout event is the authority."""
    event = frappe.get_doc("Affiliate Payout Event", event_name)
    for line in event.lines:
        frappe.db.set_value("VV Order", line.vv_order, {
            "affiliate_payout_status": "Paid",
            "affiliate_payout_batch": payout_batch,
        }, update_modified=False)
    frappe.db.set_value("Affiliate Payout Batch", payout_batch, {
        "status": "Paid", "paid_by": event.paid_by, "paid_at": event.paid_at,
        "payment_reference": event.external_reference,
    }, update_modified=False)


def payout_state(payout_batch):
    """DERIVED: is this batch paid? Answered by the event, not a status field."""
    rows = frappe.get_all("Affiliate Payout Event",
                          filters={"payout_batch": payout_batch},
                          fields=["name", "total_amount", "payment_entry",
                                  "external_reference", "paid_at"], limit=1)
    if not rows:
        return {"batch": payout_batch, "paid": False}
    r = rows[0]
    return {"batch": payout_batch, "paid": bool(r.payment_entry),
            "payout_event": r.name, "payment_entry": r.payment_entry,
            "total_amount": r.total_amount, "paid_at": r.paid_at}
