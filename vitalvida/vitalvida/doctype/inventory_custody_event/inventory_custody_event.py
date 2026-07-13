import frappe
from frappe.model.document import Document
class InventoryCustodyEvent(Document):
    def before_insert(self):
        if not self.source_key: frappe.throw("source_key is required")
    def before_save(self):
        if not self.is_new():
            old=self.get_doc_before_save()
            immutable={"source_key","event_type","source_doctype","source_name","occurred_at","payload_json"}
            if old and any(getattr(old,f,None)!=getattr(self,f,None) for f in immutable):
                frappe.throw("Inventory custody events are immutable.", frappe.PermissionError)
    def on_trash(self): frappe.throw("Inventory custody events cannot be deleted.", frappe.PermissionError)
