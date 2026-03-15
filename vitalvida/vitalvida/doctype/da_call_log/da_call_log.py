import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class DACallLog(Document):
    def before_insert(self):
        self.actioned_by = frappe.session.user
        self.actioned_at = now_datetime()
        # Populate DA display fields so manager can dial immediately
        da = frappe.get_doc("Delivery Agent", self.delivery_agent)
        self.da_name_display = da.agent_name
        self.da_phone_display = da.phone

    def before_save(self):
        if not self.is_new():
            frappe.throw("DA Call Log records are immutable.", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("DA Call Log records cannot be deleted.", frappe.PermissionError)
