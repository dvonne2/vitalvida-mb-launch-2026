# Copyright (c) 2026, azeez and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class FeePaymentRequest(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		amount: DF.Currency
		approved_at: DF.Datetime | None
		approved_by: DF.Link | None
		days_waiting: DF.Int
		delivery_agent: DF.Link
		order: DF.Link | None
		orders: DF.LongText | None
		paid_at: DF.Datetime | None
		paid_by: DF.Link | None
		payment_reference: DF.Data | None
		proof_url: DF.Data | None
		rejection_reason: DF.Text | None
		requested_at: DF.Datetime | None
		status: DF.Literal["Pending", "Approved", "Paid", "Rejected", "Disputed"]
		total_amount: DF.Currency
		transfer_reference: DF.Data | None
	# end: auto-generated types
	pass
