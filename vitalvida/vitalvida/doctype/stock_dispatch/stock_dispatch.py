"""
Stock Dispatch DocType Controller — M12

Represents a batch of stock sent from main warehouse to a DA.
On submit: creates DA Stock Entry per item, sets status = Confirmed.
"""
import frappe
from frappe.model.document import Document


class StockDispatch(Document):

	def on_submit(self):
		"""
		On submit: for each item, create DA Stock Entry (Dispatch/In).
		DA Stock Entry after_insert automatically updates DA Warehouse.
		"""
		from vitalvida.stock import dispatch_stock
		dispatch_stock(self.name)
