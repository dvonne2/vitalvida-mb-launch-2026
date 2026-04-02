"""
Delivery Agent DocType Controller
M16: recompute_stats() fixed to use Paid status (DSR) instead of Delivered.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class DeliveryAgent(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		active: DF.Check
		agent_name: DF.Data
		current_stock: DF.Int
		dsr_adjusted: DF.Percent
		dsr_colour: DF.Data | None
		dsr_strict: DF.Percent
		is_double_risk: DF.Check
		partnership_level: DF.Data | None
		phone: DF.Data
		shrinkage_rate: DF.Percent
		state: DF.Data
		strike_count: DF.Int
		strike_status: DF.Literal["", "Active", "Suspended"]
		success_rate: DF.Percent
		total_earned: DF.Currency
		total_orders: DF.Int
		zone: DF.Link | None
	# end: auto-generated types

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
		Recompute total_orders, success_rate, and all DSR fields.
		M16 FIX: Uses Paid status only — never Delivered.
		CRITICAL: success_rate now equals dsr_strict.
		"""
		try:
			from vitalvida.dsr import (
				compute_da_dsr, compute_da_shrinkage,
				get_dsr_colour, is_double_risk
			)
			from frappe.utils import today, get_first_day_of_week, add_days

			total = frappe.db.count("VV Order", {"delivery_agent": self.name})

			week_start = str(get_first_day_of_week(today()))
			week_end = str(add_days(week_start, 6))

			dsr = compute_da_dsr(self.name, week_start, week_end)
			shrinkage = compute_da_shrinkage(self.name, week_start, week_end)
			colour = get_dsr_colour(dsr["dsr_strict"])
			double_risk = is_double_risk(dsr["dsr_strict"], shrinkage)

			frappe.db.set_value("Delivery Agent", self.name, {
				"total_orders": total,
				"success_rate": round(dsr["dsr_strict"], 2),
				"dsr_strict": round(dsr["dsr_strict"], 2),
				"dsr_adjusted": round(dsr["dsr_adjusted"], 2),
				"shrinkage_rate": round(shrinkage, 2),
				"dsr_colour": colour,
				"is_double_risk": 1 if double_risk else 0,
			})

		except Exception as e:
			frappe.log_error(str(e), "Delivery Agent Stats Error")
