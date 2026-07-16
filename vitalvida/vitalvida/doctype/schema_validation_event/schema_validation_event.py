import frappe
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete

FROZEN_FIELDS = {'source_key', 'schema_definition', 'schema_hash', 'payload_hash',
                 'payload_json', 'result', 'errors_json', 'validated_at'}


class SchemaValidationEvent(Document):
    def validate(self):
        guard_immutable(self, FROZEN_FIELDS)

    def on_trash(self):
        guard_no_delete(self)
