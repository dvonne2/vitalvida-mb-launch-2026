"""
Gap 9 — Cycle Count with ABC Classification
A items = counted weekly, B = monthly, C = quarterly.
System quantity auto-populated on count start. Variance auto-computed.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class CycleCountSchedule(Document):
    def before_save(self):
        if self.is_new():
            return
        doc_before = self.get_doc_before_save()
        if not doc_before:
            return

        # Auto-populate system quantity when count starts
        if doc_before.count_status == "Scheduled" and self.count_status == "In Progress":
            self._populate_system_qty()
            self.started_at = now_datetime()
            self.counted_by = frappe.session.user

        # Compute variance when completed
        if doc_before.count_status != "Completed" and self.count_status == "Completed":
            self._compute_variance()
            self.completed_at = now_datetime()

        # Record verifier
        if doc_before.count_status != "Verified" and self.count_status == "Verified":
            self.verified_by = frappe.session.user
            self.verified_at = now_datetime()

    def _populate_system_qty(self):
        wh = frappe.db.exists("DA Warehouse", {
            "delivery_agent": self.delivery_agent,
            "product": self.product
        })
        self.system_quantity = float(
            frappe.db.get_value("DA Warehouse", wh, "current_stock") or 0
        ) if wh else 0.0

    def _compute_variance(self):
        sys_qty = float(self.system_quantity or 0)
        counted = float(self.counted_quantity or 0)
        self.variance_quantity = round(sys_qty - counted, 2)
        self.variance_percentage = round(
            abs(self.variance_quantity) / sys_qty * 100, 2
        ) if sys_qty > 0 else 0.0

    def on_trash(self):
        if self.count_status in ("Completed", "Verified"):
            frappe.throw("Completed cycle counts cannot be deleted.", frappe.PermissionError)
