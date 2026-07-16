"""Consumer Activation Request — immutable approval evidence.

This record is EVIDENCE, not runtime configuration. It states what was proposed
and approved at a point in time. It never answers "is this consumer live?" —
Event Consumer Map is the single runtime authority for that.
"""
import frappe
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete

FROZEN_FIELDS = {'request_key', 'package_name', 'parent_event_key', 'proposed_consumer_module', 'proposed_consumer_method', 'proposed_read_mode', 'proposed_delivery', 'authoritative_source_doctype', 'authoritative_source_description', 'authoritative_consequence', 'justification', 'change_hash', 'requested_by', 'requested_at'}


class ConsumerActivationRequest(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
