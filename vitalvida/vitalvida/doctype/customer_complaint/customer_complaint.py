import frappe
from frappe.model.document import Document
from frappe.utils import time_diff_in_seconds


class CustomerComplaint(Document):
    def validate(self):
        # compute resolution hours + reflect resolved timestamp when status hits Resolved/Closed
        if self.status in ("Resolved", "Closed") and not self.resolved_at:
            self.resolved_at = frappe.utils.now_datetime()
        if self.resolved_at and self.opened_at:
            secs = time_diff_in_seconds(self.resolved_at, self.opened_at)
            self.resolution_hours = round(max(0, secs) / 3600.0, 2)
