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
         erpnext_consequence="Journal Entry (accrue expense + payable)",
         policy_ref="AFF-001"),
    dict(event_key="vv.affiliate.payout_settled",
         event_name="Affiliate Payout Settled", bucket="B",
         authoritative_doctype="Affiliate Payout Event",
         producer_module="vitalvida.affiliate",
         erpnext_consequence="Payment Entry (settle payable from bank)",
         policy_ref="AFF-002"),
]


def execute():
    if not frappe.db.exists("DocType", "VV Finance Config"):
        frappe.throw("VV Finance Config missing — install Package 08 first.")
    create_custom_fields({"VV Finance Config": [
        {"fieldname": "affiliate_commission_expense_account", "fieldtype": "Link",
         "options": "Account", "label": "Affiliate Commission Expense",
         "description": "Debited when a media buyer earns commission. "
                        "Unmapped = affiliate commission refuses to post."},
        {"fieldname": "affiliate_commission_payable_account", "fieldtype": "Link",
         "options": "Account", "label": "Affiliate Commission Payable",
         "description": "Credited on earning, debited on payout. This is what "
                        "you owe media buyers."},
    ]}, ignore_validate=True)
    register_events(EVENT_DEFINITIONS)
