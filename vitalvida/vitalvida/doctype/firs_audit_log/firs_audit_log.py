import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class FIRSAuditLog(Document):
    def before_insert(self):
        self.timestamp = now_datetime()
    def before_save(self):
        if not self.is_new():
            frappe.throw("FIRS Audit Log records are immutable.", frappe.PermissionError)
    def on_trash(self):
        frappe.throw("FIRS Audit Log records cannot be deleted.", frappe.PermissionError)
