"""Affiliate Commission Event — immutable authoritative record.

The consequence link (journal_entry / payment_entry) is written once by the
Package 17 consequence writer and never changed. Substantive fields are frozen:
a correction is a new event, never a rewrite (GOV-004).
"""
import frappe
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete

FROZEN_FIELDS = {'source_key', 'vv_order', 'media_buyer', 'commission_rule', 'rule_version', 'rule_payload_json', 'rule_payload_hash', 'commission_amount', 'earned_on', 'computed_at'}


class AffiliateCommissionEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
