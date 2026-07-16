"""Tax Exception — immutable Package 14 tax evidence."""
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete, require_distinct_users

FROZEN_FIELDS = {'source_key', 'variance_amount', 'source_doctype', 'actual_amount', 'tax_type', 'source_name', 'reason_code', 'severity', 'tax_calculation_snapshot', 'opened_by_service', 'expected_amount', 'opened_at'}

class TaxException(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
