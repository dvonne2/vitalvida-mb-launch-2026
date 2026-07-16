"""Tax Authority Snapshot Event — immutable Package 14 tax evidence."""
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete, require_distinct_users

FROZEN_FIELDS = {'source_key', 'jurisdiction', 'authority_payload_json', 'tax_type', 'captured_at', 'authority_doctype', 'captured_by_service', 'authority_version', 'effective_date', 'authority_payload_hash', 'authority_name'}

class TaxAuthoritySnapshotEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
