"""Tax Reconciliation Snapshot Event — immutable Package 14 tax evidence."""
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete, require_distinct_users

FROZEN_FIELDS = {'source_key', 'reconciled_at', 'variance_amount', 'evidence_hash', 'reconciled_by_service', 'tax_type', 'gl_amount', 'invoiced_amount', 'paid_amount', 'source_documents_hash', 'period_end', 'period_start', 'expected_amount'}

class TaxReconciliationSnapshotEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
