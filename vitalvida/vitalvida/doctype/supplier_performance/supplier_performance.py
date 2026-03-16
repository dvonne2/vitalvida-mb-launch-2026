import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class SupplierPerformance(Document):
    def before_save(self):
        self._compute_scores()
        self.computed_at = now_datetime()

    def _compute_scores(self):
        placed = int(self.total_orders_placed or 0)
        fulfilled = int(self.total_orders_fulfilled or 0)
        on_time = int(self.on_time_deliveries or 0)
        defects = int(self.defect_count or 0)

        self.fulfillment_rate = round(fulfilled / placed * 100, 1) if placed > 0 else 0.0
        self.on_time_rate = round(on_time / fulfilled * 100, 1) if fulfilled > 0 else 0.0
        defect_rate = round(defects / fulfilled * 100, 1) if fulfilled > 0 else 0.0
        self.quality_score = round(max(100 - defect_rate, 0), 1)

        # Weighted: 40% fulfillment + 30% on-time + 30% quality
        self.overall_score = round(
            float(self.fulfillment_rate) * 0.4 +
            float(self.on_time_rate) * 0.3 +
            float(self.quality_score) * 0.3,
            1
        )
