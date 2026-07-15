"""Affiliate Payout Event — immutable authoritative record.

The consequence link (journal_entry / payment_entry) is written once by the
Package 17 consequence writer and never changed. Substantive fields are frozen:
a correction is a new event, never a rewrite (GOV-004).
"""
import frappe
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete

FROZEN_FIELDS = {'source_key', 'payout_batch', 'media_buyer', 'period_start', 'period_end', 'total_amount', 'lines_hash', 'external_reference', 'paid_on', 'paid_at', 'paid_by'}


class AffiliatePayoutEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
