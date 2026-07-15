"""COA Drift Event — immutable, read-only audit evidence.

Records how the live ERPNext Account tree differs from the version-controlled
expected structure at a point in time. Creates and modifies NOTHING: ERPNext
Account remains the sole Chart of Accounts authority.
"""
import frappe
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete

FROZEN_FIELDS = {'source_key', 'company', 'expected_version', 'expected_hash',
                 'drift_count', 'missing_json', 'extra_json', 'mismatched_json',
                 'audited_at'}


class COADriftEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
