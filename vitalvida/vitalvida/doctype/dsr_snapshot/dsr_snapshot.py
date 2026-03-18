import frappe
from frappe.model.document import Document


class DSRSnapshot(Document):
    def before_save(self):
        if not self.is_new():
            frappe.throw(
                "DSR Snapshot records are immutable and cannot be edited.",
                frappe.PermissionError
            )

    def on_trash(self):
        frappe.throw(
            "DSR Snapshot records cannot be deleted.",
            frappe.PermissionError
        )
