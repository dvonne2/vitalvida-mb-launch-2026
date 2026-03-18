import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class DamageAssessment(Document):
    def before_insert(self):
        self.assessed_by = frappe.session.user
        self.assessed_at = now_datetime()
    def on_trash(self):
        frappe.throw("Damage Assessment records cannot be deleted.", frappe.PermissionError)
