"""Read-only affiliate projections — computed, never stored.

"What do we owe media buyers?" is answered from the authoritative events and the
ERPNext ledger, never by recalculating from VV Order fields.
"""
import frappe


def outstanding_payable(media_buyer=None):
    """Accrued but unpaid commission. Derived from events, not cached anywhere."""
    cond = "AND e.media_buyer = %(buyer)s" if media_buyer else ""
    return frappe.db.sql(f"""
        SELECT e.media_buyer, COUNT(*) AS orders,
               COALESCE(SUM(e.commission_amount), 0) AS payable
          FROM `tabAffiliate Commission Event` e
         WHERE e.journal_entry IS NOT NULL AND e.journal_entry != ''
           AND NOT EXISTS (SELECT 1 FROM `tabAffiliate Payout Line` l
                            WHERE l.commission_event = e.name)
           {cond}
         GROUP BY e.media_buyer
    """, {"buyer": media_buyer}, as_dict=True)


def unaccrued_commission():
    """CONTROL: earned events with no Journal Entry. Should always be empty."""
    return frappe.get_all("Affiliate Commission Event",
                          filters={"journal_entry": ["in", [None, ""]]},
                          fields=["name", "vv_order", "media_buyer",
                                  "commission_amount", "earned_on"])


def orders_paid_without_event():
    """CONTROL: legacy rows marked Paid with no authoritative payout event.

    Should always be zero. A non-zero result means money moved outside the ledger.
    """
    return frappe.db.sql("""
        SELECT o.name AS vv_order, o.media_buyer, o.affiliate_commission_amount
          FROM `tabVV Order` o
         WHERE o.affiliate_payout_status = 'Paid'
           AND NOT EXISTS (SELECT 1 FROM `tabAffiliate Payout Line` l
                            WHERE l.vv_order = o.name)
    """, as_dict=True)
