import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class KYCDocumentLog(Document):
    def before_insert(self):
        self.submitted_at = now_datetime()
    def before_save(self):
        if not self.is_new():
            frappe.throw("KYC Document Log records are immutable.", frappe.PermissionError)
    def on_trash(self):
        frappe.throw("KYC Document Log records cannot be deleted.", frappe.PermissionError)
