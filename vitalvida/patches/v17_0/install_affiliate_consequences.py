"""Package 17 — Affiliate Consequences.

Registers the two affiliate business events and adds the affiliate accounts to
VV Finance Config. Seeds nothing, pays nothing, posts nothing. The writers are
LIVE but fail closed: until an accountant maps the accounts, any attempt to earn
or pay commission refuses loudly rather than posting to a guessed account.
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from vitalvida.patches._register import register_events

EVENT_DEFINITIONS = [
    dict(event_key="vv.affiliate.commission_earned",
         event_name="Affiliate Commission Earned", bucket="B",
         authoritative_doctype="Affiliate Commission Event",
         producer_module="vitalvida.affiliate",
         erpnext_consequence="Purchase Invoice (accrue: supplier owed)",
         policy_ref="AFF-001"),
    dict(event_key="vv.affiliate.payout_settled",
         event_name="Affiliate Payout Settled", bucket="B",
         authoritative_doctype="Affiliate Payout Event",
         producer_module="vitalvida.affiliate",
         erpnext_consequence="Payment Entry (settle: references the Purchase Invoice)",
         policy_ref="AFF-002"),
]


def execute():
    if not frappe.db.exists("DocType", "VV Finance Config"):
        frappe.throw("VV Finance Config missing — install Package 08 first.")
    _link_media_buyer_to_supplier()
    create_custom_fields({"VV Finance Config": [
        {"fieldname": "affiliate_commission_expense_account", "fieldtype": "Link",
         "options": "Account", "label": "Affiliate Commission Expense",
         "description": "Debited when a media buyer earns commission. "
                        "Unmapped = affiliate commission refuses to post."},
        {"fieldname": "affiliate_commission_payable_account", "fieldtype": "Link",
         "options": "Account", "label": "Affiliate Commission Payable",
         "description": "The Purchase Invoice credit_to account. Party-tracked, "
                        "so what you owe each buyer is read from the ERPNext "
                        "party ledger, never recalculated. Must be account_type "
                        "= Payable."},
        {"fieldname": "affiliate_commission_item", "fieldtype": "Link",
         "options": "Item", "label": "Affiliate Commission Item",
         "description": "Item row on the commission Purchase Invoice, mirroring "
                        "da_fee_item. The expense account is set explicitly on "
                        "the row, so this is a label only."},
    ]}, ignore_validate=True)
    register_events(EVENT_DEFINITIONS)


def _link_media_buyer_to_supplier():
    """Give each media buyer a Supplier party.

    Affiliate commission posts to a Payable-type account, which ERPNext requires
    a party for. The party is also what lets "what do we owe Media Buyer X?" be
    read from the ERPNext party ledger rather than recalculated from events —
    exactly as Package 09 already does for delivery agents via
    Delivery Agent.supplier.

    The field is created empty. Provisioning each Supplier is a deliberate action;
    until then commission for that buyer refuses to post.
    """
    if not frappe.db.exists("DocType", "VV Media Buyer"):
        frappe.throw("VV Media Buyer missing — the media buyer program is not installed.")
    create_custom_fields({"VV Media Buyer": [
        {"fieldname": "supplier", "fieldtype": "Link", "options": "Supplier",
         "label": "Supplier (party for commission payable)",
         "description": "The ERPNext Supplier this buyer is paid as. Commission "
                        "cannot be earned or paid without it: the payable account "
                        "is party-tracked, so the amount owed is read from the "
                        "party ledger, never recalculated."},
    ]}, ignore_validate=True)
