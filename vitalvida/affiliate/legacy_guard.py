"""Close the hole: money may not move without a ledger entry.

The legacy `vitalvida.media_buyer.mark_batch_paid()` sets Affiliate Payout Batch
status to Paid and raw-SQL-updates VV Order rows, creating NO Payment Entry, no
Journal Entry, no GL Entry. It also stores payout state in two places at once.
That is two writers for "the affiliate was paid", and zero authoritative
consequences.

This guard makes Package 17 the ONE writer. It is bound to Affiliate Payout
Batch via doc_events, so it fires however the batch is saved — legacy function,
Desk UI, API or console. There is no path around it.

It does NOT change media_buyer.py. Legacy code keeps running; it simply can no
longer mark a batch Paid without the consequence Package 17 posts.
"""
import frappe


def guard_payout_batch(doc, method=None):
    """Refuse Paid status unless an Affiliate Payout Event posted a Payment Entry."""
    if doc.get("status") != "Paid":
        return
    before = doc.get_doc_before_save()
    if before and before.get("status") == "Paid":
        return  # already paid; not a new transition

    event = frappe.db.get_value(
        "Affiliate Payout Event", {"payout_batch": doc.name},
        ["name", "payment_entry"], as_dict=True)

    if not event:
        frappe.throw(
            f"Payout batch {doc.name} cannot be marked Paid directly.\n\n"
            "Affiliate commission must be paid through Package 17 so the money "
            "is recorded in the ledger:\n"
            "    vitalvida.affiliate.payout.record_payout(batch, external_reference)\n\n"
            "That posts the authoritative Payment Entry and then marks this batch "
            "Paid. Marking it Paid directly would move money with no accounting "
            "entry — the commission would be missing from the P&L.",
            frappe.ValidationError)

    if not event.payment_entry:
        frappe.throw(
            f"Payout event {event.name} exists but posted no Payment Entry. "
            "Refusing to mark the batch Paid: the ledger would not reflect the "
            "payment. Check VV Finance Config affiliate accounts.",
            frappe.ValidationError)


def guard_order_payout_status(doc, method=None):
    """Refuse marking a VV Order affiliate-Paid without an accrued commission event."""
    if doc.get("affiliate_payout_status") != "Paid":
        return
    before = doc.get_doc_before_save()
    if before and before.get("affiliate_payout_status") == "Paid":
        return
    accrued = frappe.db.get_value("Affiliate Commission Event",
                                  {"vv_order": doc.name}, ["name", "journal_entry"],
                                  as_dict=True)
    if not accrued or not accrued.journal_entry:
        frappe.throw(
            f"Order {doc.name} cannot be marked affiliate-Paid: no accrued "
            "Affiliate Commission Event with a posted Journal Entry exists. "
            "Commission must be earned and accrued before it is paid.",
            frappe.ValidationError)
