import frappe
from frappe.model.document import Document

class VVMediaBuyer(Document):
    def validate(self):
        self._check_slot_cap()

    def _check_slot_cap(self):
        """Block new buyer creation if max_active_buyers cap is hit."""
        if not self.is_new():
            return
        try:
            settings = frappe.get_single("Vitalvida Settings")
            cap = int(getattr(settings, "max_active_buyers", None) or 0)
            if cap <= 0:
                return
            current = frappe.db.count("VV Media Buyer", {"is_active": 1})
            if current >= cap:
                frappe.throw(
                    f"Maximum active media buyer slots ({cap}) reached. "
                    f"Cannot add new buyers until a slot opens."
                )
        except frappe.DoesNotExistError:
            pass
