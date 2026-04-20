# Copyright (c) 2026, azeez and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class Order(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		address: DF.Text
		attempt_count: DF.Int
		brand: DF.Data | None
		call_back_time: DF.Datetime | None
		cancellation_source: DF.Data | None
		customer_name: DF.Data
		customer_phone: DF.Data
		delivered_at: DF.Datetime | None
		delivery_agent: DF.Data | None
		delivery_fee: DF.Currency
		expected_delivery_date: DF.Date | None
		landmark: DF.Data | None
		lga: DF.Data | None
		order_status: DF.Literal["Pending", "Confirmed", "In Transit", "Delivered", "Cancelled", "Rescheduled"]
		package_name: DF.Data | None
		paid_at: DF.Datetime | None
		product_amount: DF.Currency
		reschedule_note: DF.Text | None
		state: DF.Data | None
		telesales_closer: DF.Data | None
		total_payable: DF.Currency
	# end: auto-generated types
	pass
