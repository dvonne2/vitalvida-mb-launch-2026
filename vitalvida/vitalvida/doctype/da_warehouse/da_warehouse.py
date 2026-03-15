"""
DA Warehouse DocType Controller — M12

One record per Delivery Agent per product.
Tracks their live stock position.

current_stock is NEVER editable manually — only DA Stock Entry inserts
may update it via stock.py._create_stock_entry().
Only is_frozen and freeze_reason can be updated by users.
"""
import frappe
from frappe.model.document import Document


class DAWarehouse(Document):

	def before_save(self):
		"""Block manual edits to current_stock."""
		if self.is_new():
			return

		doc_before = self.get_doc_before_save()
		if not doc_before:
			return

		if float(self.current_stock or 0) != float(doc_before.current_stock or 0):
			frappe.throw(
				frappe._(
					"current_stock cannot be edited manually. "
					"It is updated only by DA Stock Entry inserts."
				),
				frappe.PermissionError
			)
