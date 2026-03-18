import json
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

class CustomerSegment(Document):
    def validate(self):
        # Validate JSON
        try:
            json.loads(self.filter_criteria or "{}")
        except (json.JSONDecodeError, TypeError):
            frappe.throw("Filter Criteria must be valid JSON.")

    @frappe.whitelist()
    def refresh_count(self):
        """Refresh the customer count based on filter criteria."""
        phones = self.get_matching_phones()
        self.customer_count = len(phones)
        self.last_refreshed = now_datetime()
        self.save(ignore_permissions=True)
        return self.customer_count

    def get_matching_phones(self) -> list:
        """Return list of customer phones matching this segment's criteria."""
        try:
            criteria = json.loads(self.filter_criteria or "{}")
        except (json.JSONDecodeError, TypeError):
            return []

        filters = {"order_status": ["!=", "Partial"]}
        if "status" in criteria:
            filters["order_status"] = criteria["status"]

        orders = frappe.get_all("VV Order", filters=filters,
                                 fields=["customer_phone"], group_by="customer_phone")
        return [o.customer_phone for o in orders if o.customer_phone]
