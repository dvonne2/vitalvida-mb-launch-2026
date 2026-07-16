"""Tax Resolution Event — immutable Package 14 tax evidence."""
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete, require_distinct_users

FROZEN_FIELDS = {'tax_exception', 'source_key', 'evidence_hash', 'accounting_reference_name', 'resolved_by', 'accounting_reference_doctype', 'supporting_document', 'resolved_at', 'approved_by', 'resolution_type', 'resolution_note', 'approval_event'}

class TaxResolutionEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)
        require_distinct_users(self.resolved_by, self.approved_by, "approve")

    def on_trash(self):
        guard_no_delete(self)
