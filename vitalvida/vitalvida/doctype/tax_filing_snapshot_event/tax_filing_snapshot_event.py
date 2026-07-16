"""Tax Filing Snapshot Event — immutable Package 14 tax evidence."""
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete, require_distinct_users

FROZEN_FIELDS = {'source_key', 'jurisdiction', 'evidence_hash', 'snapshot_at', 'tax_type', 'tax_total', 'amends_snapshot', 'reviewed_by', 'source_documents_hash', 'period_end', 'period_start', 'approved_by', 'paid_total', 'variance_total', 'filing_reference', 'taxable_basis_total', 'prepared_by', 'authority_snapshots_json', 'review_approval_event', 'final_approval_event'}

class TaxFilingSnapshotEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
