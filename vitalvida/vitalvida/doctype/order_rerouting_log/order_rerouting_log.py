import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class OrderReroutingLog(Document):
    def before_insert(self):
        self.rerouted_by = frappe.session.user
        self.rerouted_at = now_datetime()
    def before_save(self):
        if not self.is_new():
            frappe.throw("Order Rerouting Log records are immutable.", frappe.PermissionError)
    def on_trash(self):
        frappe.throw("Order Rerouting Log records cannot be deleted.", frappe.PermissionError)
