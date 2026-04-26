"""
DA Stock Entry DocType Controller — M12

Immutable ledger — every stock movement in or out of a DA's holding.
No field can be edited after insert. No record can be deleted.

On before_insert:
  - Duplicate deduction guard (Deduction + reference_order)
  - Stamp entry_date and posted_by

On after_insert:
  - Update DA Warehouse current_stock (via stock.py)
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class DAStockEntry(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		balance_after: DF.Float
		balance_before: DF.Float
		delivery_agent: DF.Link
		direction: DF.Literal["In", "Out"]
		entry_date: DF.Datetime
		entry_type: DF.Literal["Dispatch", "Deduction", "Return", "Adjustment"]
		notes: DF.Text | None
		posted_by: DF.Link | None
		product: DF.Link
		quantity: DF.Float
		reference_dispatch: DF.Link | None
		reference_order: DF.Link | None
	# end: auto-generated types

	def before_insert(self):
		"""Stamp metadata and run duplicate deduction guard."""
		self.entry_date = now_datetime()
		self.posted_by = frappe.session.user

		# ── Duplicate deduction guard ─────────────────────────────────────
		if self.entry_type == "Deduction" and self.reference_order:
			if frappe.db.exists("DA Stock Entry", {
				"entry_type": "Deduction",
				"reference_order": self.reference_order,
				"delivery_agent": self.delivery_agent
			}):
				frappe.throw(
					f"A deduction already exists for order {self.reference_order} "
					f"by DA {self.delivery_agent}. Duplicate delivery payment blocked.",
					frappe.DuplicateEntryError
				)

	def before_save(self):
		"""Block all edits after initial insert — fully immutable."""
		if self.is_new():
			return

		frappe.throw(
			frappe._("DA Stock Entries are immutable and cannot be edited."),
			frappe.PermissionError
		)

	def after_insert(self):
		"""Update DA Warehouse current_stock after entry is committed."""
		try:
			from vitalvida.stock import _update_warehouse_stock
			_update_warehouse_stock(self)
		except Exception as e:
			frappe.log_error(
				f"M12 DA Warehouse update failed for entry {self.name}: {str(e)}",
				"M12 Stock Entry Error"
			)

	def on_trash(self):
		frappe.throw(
			frappe._("DA Stock Entries cannot be deleted."),
			frappe.PermissionError
		)
