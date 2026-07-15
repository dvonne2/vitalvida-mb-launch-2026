"""Consumer Activation Reversal Event — immutable approval evidence.

This record is EVIDENCE, not runtime configuration. It states what was proposed
and approved at a point in time. It never answers "is this consumer live?" —
Event Consumer Map is the single runtime authority for that.
"""
import frappe
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete

FROZEN_FIELDS = {'source_key', 'activation_request', 'consumer_activation_event', 'removed_child_row', 'reason', 'reversed_by', 'reversed_at'}


class ConsumerActivationReversalEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
