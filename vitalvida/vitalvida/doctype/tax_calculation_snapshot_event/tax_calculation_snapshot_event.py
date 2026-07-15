"""Tax Calculation Snapshot Event — immutable Package 14 tax evidence."""
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete, require_distinct_users

FROZEN_FIELDS = {'input_payload_json', 'actual_tax', 'source_name', 'transaction_date', 'input_payload_hash', 'expected_tax', 'result', 'tax_authority_snapshot', 'source_key', 'tax_type', 'taxable_basis', 'calculated_at', 'erpnext_reference_name', 'calculated_by_service', 'variance', 'calculation_output_hash', 'source_doctype', 'currency', 'source_event_key', 'rate_or_band_reference', 'erpnext_reference_doctype'}

class TaxCalculationSnapshotEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
