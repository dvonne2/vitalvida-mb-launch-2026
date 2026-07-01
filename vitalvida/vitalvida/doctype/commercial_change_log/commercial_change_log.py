import frappe
from frappe.model.document import Document


class CommercialChangeLog(Document):
    """Immutable append-only ledger of every commercial change to an order."""

    def before_save(self):
        if not self.is_new():
            frappe.throw("Commercial Change Log entries are immutable.",
                         frappe.PermissionError)

    def on_trash(self):
        frappe.throw("Commercial Change Log entries cannot be deleted.",
                     frappe.PermissionError)
