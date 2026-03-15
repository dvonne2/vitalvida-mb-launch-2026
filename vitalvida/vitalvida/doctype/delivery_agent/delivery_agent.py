"""
Delivery Agent DocType Controller
"""

import frappe
from frappe import _
from frappe.model.document import Document


class DeliveryAgent(Document):

	def validate(self):
		"""Normalize phone on save."""
		self._normalize_phone()

	def _normalize_phone(self):
		"""Normalize phone to 234XXXXXXXXXX format."""
		if self.phone:
			phone = str(self.phone).strip().replace(" ", "").replace("-", "")
			if phone.startswith("+"):
				phone = phone[1:]
			if phone.startswith("0"):
				phone = "234" + phone[1:]
			self.phone = phone

	def recompute_stats(self):
		"""
		Recompute total_orders and success_rate from VV Order records.
		Called by M6 after every terminal transition.
		"""
		try:
			total = frappe.db.count("VV Order", {"delivery_agent": self.name})

			delivered = frappe.db.count("VV Order", {
				"delivery_agent": self.name,
				"order_status": "Delivered"
			})
			cancelled = frappe.db.count("VV Order", {
				"delivery_agent": self.name,
				"order_status": "Cancelled"
			})
			returned = frappe.db.count("VV Order", {
				"delivery_agent": self.name,
				"order_status": "Returned"
			})

			denominator = delivered + cancelled + returned
			rate = (delivered / denominator * 100) if denominator > 0 else 0.0

			frappe.db.set_value("Delivery Agent", self.name, {
				"total_orders": total,
				"success_rate": round(rate, 2)
			})

		except Exception as e:
			frappe.log_error(str(e), "Delivery Agent Stats Error")
