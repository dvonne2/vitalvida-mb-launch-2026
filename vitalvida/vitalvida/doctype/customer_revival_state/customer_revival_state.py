import frappe
from frappe.model.document import Document


class CustomerRevivalState(Document):
    """One revival record per revived order. Not deletable (audit)."""

    def on_trash(self):
        frappe.throw("Customer Revival State cannot be deleted.",
                     frappe.PermissionError)
