import frappe
from frappe.model.document import Document


class RevenueBusinessEvent(Document):
    """Immutable. Once inserted it may not be edited or deleted — money follows
    these events, so they are append-only history."""

    def before_save(self):
        if not self.is_new():
            frappe.throw("Revenue Business Events are immutable and cannot be edited.",
                         frappe.PermissionError)

    def on_trash(self):
        frappe.throw("Revenue Business Events cannot be deleted.",
                     frappe.PermissionError)
