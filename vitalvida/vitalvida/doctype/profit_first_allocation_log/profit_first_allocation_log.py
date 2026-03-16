import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class ProfitFirstAllocationLog(Document):
    def before_insert(self):
        self.allocated_at = now_datetime()
    def before_save(self):
        if not self.is_new():
            frappe.throw("Allocation Log records are immutable.", frappe.PermissionError)
    def on_trash(self):
        frappe.throw("Allocation Log records cannot be deleted.", frappe.PermissionError)
