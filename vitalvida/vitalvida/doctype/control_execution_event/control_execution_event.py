import frappe
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete

FROZEN_FIELDS = {'source_key', 'control_definition', 'rule_version', 'source_doctype', 'source_name', 'input_json', 'input_hash', 'result', 'evaluated_at'}


class ControlExecutionEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
