import frappe
from frappe.model.document import Document


class StockVariance(Document):

	def before_save(self):
		"""Fully immutable after insert."""
		if self.is_new():
			return
		frappe.throw(
			frappe._("Stock Variance records are immutable and cannot be edited."),
			frappe.PermissionError
		)

	def on_trash(self):
		frappe.throw(
			frappe._("Stock Variance records cannot be deleted."),
			frappe.PermissionError
		)
