"""
Telesales Closer DocType Controller — M10
"""
import frappe
from frappe.model.document import Document


class TelesalesCloser(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		avg_confirmation_minutes: DF.Float
		closer_name: DF.Data
		dsr_colour: DF.Data | None
		dsr_strict: DF.Percent
		ghost_rate: DF.Percent
		is_active: DF.Check
		is_blocked: DF.Check
		last_assigned_at: DF.Datetime | None
		max_pending_override: DF.Int
		phone: DF.Data
		pool: DF.Literal["General", "FHG", "IR"]
		round_robin_index: DF.Int
		total_assigned_this_period: DF.Int
		total_ghosted_this_period: DF.Int
		total_paid_this_period: DF.Int
		user: DF.Link | None
		weekly_delivery_rate: DF.Float
	# end: auto-generated types
	pass
