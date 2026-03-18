"""M27: Validates that total allocation across all active buckets = 100%."""
import frappe
from frappe.model.document import Document

class ProfitFirstBucket(Document):
    def validate(self):
        self._validate_total_allocation()

    def _validate_total_allocation(self):
        total = frappe.db.sql("""
            SELECT COALESCE(SUM(allocation_percentage), 0) as total
            FROM `tabProfit First Bucket`
            WHERE is_active = 1 AND name != %s
        """, (self.name,), as_dict=True)
        current_total = float(total[0].total) if total else 0.0
        if self.is_active:
            current_total += float(self.allocation_percentage or 0)
        if abs(current_total - 100.0) > 0.01 and self.is_active:
            frappe.msgprint(
                f"Warning: Active bucket allocations total {current_total:.1f}%. "
                f"They must total exactly 100% for allocations to run.",
                indicator="orange", alert=True
            )
