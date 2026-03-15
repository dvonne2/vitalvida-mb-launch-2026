import frappe
from frappe.model.document import Document

class StockMovementLog(Document):
    def before_save(self):
        if not self.is_new():
            frappe.throw("Stock Movement Log records are immutable.", frappe.PermissionError)
    def on_trash(self):
        frappe.throw("Stock Movement Log records cannot be deleted.", frappe.PermissionError)
