import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class LMSOverrideLog(Document):
    def before_insert(self):
        self.overridden_by = frappe.session.user
        self.overridden_at = now_datetime()
    def before_save(self):
        if not self.is_new():
            frappe.throw("LMS Override Log records are immutable.", frappe.PermissionError)
    def on_trash(self):
        frappe.throw("LMS Override Log records cannot be deleted.", frappe.PermissionError)
