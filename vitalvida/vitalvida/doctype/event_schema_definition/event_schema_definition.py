import frappe
from frappe.model.document import Document
from vitalvida.governance.immutable import guard_immutable, guard_no_delete

FROZEN_FIELDS = {'schema_full_key', 'schema_name', 'schema_version', 'schema_json'}


class EventSchemaDefinition(Document):
    def validate(self):
        # Event Schema Definition: immutable ONCE ACTIVE (rule 10).
        if self.is_new():
            return
        before = self.get_doc_before_save()
        if before and before.get("status") == "Active":
            guard_immutable(self, FROZEN_FIELDS | {"status"})

    def on_trash(self):
        if self.get("status") == "Active":
            guard_no_delete(self)
