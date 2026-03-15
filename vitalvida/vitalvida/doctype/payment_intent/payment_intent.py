"""
Payment Intent DocType Controller — M6
Auto-created on VV Order creation.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, add_to_date


class PaymentIntent(Document):

	def before_insert(self):
		"""Auto-generate payment_reference and set expires_at."""
		self._generate_payment_reference()
		self._set_expires_at()

	# ─── Generators ────────────────────────────────────────────────────

	def _generate_payment_reference(self):
		"""
		Format: FHG-{ORDER_ID}-{LAST4PHONE}
		Example: FHG-VV-ORD-2026-00001-5685
		"""
		if self.payment_reference:
			return  # Already set

		order_id = self.order or ""
		phone = self.customer_phone or ""

		# Get last 4 digits of phone
		digits_only = "".join(filter(str.isdigit, phone))
		last4 = digits_only[-4:] if len(digits_only) >= 4 else digits_only.zfill(4)

		self.payment_reference = f"FHG-{order_id}-{last4}"

	def _set_expires_at(self):
		"""Set expires_at to 48 hours from now."""
		self.expires_at = add_to_date(now_datetime(), hours=48)
