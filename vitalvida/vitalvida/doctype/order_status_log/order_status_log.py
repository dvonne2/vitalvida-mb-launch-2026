import frappe
from frappe.model.document import Document


class OrderStatusLog(Document):
    def before_save(self):
        if not self.is_new():
            frappe.throw(
                "Order Status Log records are immutable and cannot be edited.",
                frappe.PermissionError
            )

    def on_trash(self):
        frappe.throw(
            "Order Status Log records cannot be deleted.",
            frappe.PermissionError
        )
