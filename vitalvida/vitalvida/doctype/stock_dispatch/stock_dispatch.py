"""
Stock Dispatch DocType Controller — M12

Represents a batch of stock sent from main warehouse to a DA.
On submit: creates DA Stock Entry per item, sets status = Confirmed.
"""
import frappe
from frappe.model.document import Document


class StockDispatch(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF
		from vitalvida.vitalvida.doctype.stock_dispatch_item.stock_dispatch_item import StockDispatchItem

		amended_from: DF.Link | None
		approval_required: DF.Check
		da_pickup_transport: DF.Currency
		delivery_agent: DF.Link
		dispatch_date: DF.Date
		dispatched_by: DF.Link | None
		driver_phone: DF.Phone
		driver_transport: DF.Currency
		eta_date: DF.Date
		items: DF.Table[StockDispatchItem]
		motor_park: DF.Data
		notes: DF.Text | None
		rejection_reason: DF.LongText | None
		status: DF.Literal["Pending", "Confirmed", "Partially Returned"]
		storekeeper_fee: DF.Currency
		total_cost: DF.Currency
	# end: auto-generated types

	def on_submit(self):
		"""
		On submit: for each item, create DA Stock Entry (Dispatch/In).
		DA Stock Entry after_insert automatically updates DA Warehouse.
		"""
		from vitalvida.stock import dispatch_stock
		dispatch_stock(self.name)
