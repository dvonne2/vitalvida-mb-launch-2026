import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, getdate, today

class TrainingSchedule(Document):
    def before_insert(self):
        self.scheduled_by = frappe.session.user
        self.scheduled_at = now_datetime()

    def validate(self):
        if getdate(self.scheduled_date) <= getdate(today()):
            frappe.throw("Scheduled date must be a future date.")

    def on_trash(self):
        frappe.throw("Training Schedule records cannot be deleted.", frappe.PermissionError)
