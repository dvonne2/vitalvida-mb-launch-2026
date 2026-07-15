import frappe
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete

FROZEN_FIELDS = {'source_key', 'control_exception', 'resolution', 'resolution_hash', 'resolved_at', 'resolved_by'}


class ControlResolutionEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
