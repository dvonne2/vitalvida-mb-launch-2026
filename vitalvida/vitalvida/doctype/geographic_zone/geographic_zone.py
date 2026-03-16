import frappe
from frappe.model.document import Document

class GeographicZone(Document):
    def get_states_list(self):
        return [s.strip() for s in (self.states_included or "").split(",") if s.strip()]
