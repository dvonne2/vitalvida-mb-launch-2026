import frappe
from frappe.model.document import Document

class WhatsAppChannel(Document):
    def validate(self):
        if self.is_default:
            # Only one default allowed
            others = frappe.db.get_all("WhatsApp Channel",
                filters={"is_default": 1, "name": ["!=", self.name]},
                fields=["name"])
            for o in others:
                frappe.db.set_value("WhatsApp Channel", o.name, "is_default", 0)
