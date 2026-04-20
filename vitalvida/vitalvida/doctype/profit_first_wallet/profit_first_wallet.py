# Copyright (c) 2026, azeez and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class ProfitFirstWallet(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		allocated_percentage: DF.Float
		amount: DF.Currency
		current_balance: DF.Currency
		notes: DF.Text | None
		wallet_type: DF.Literal["Income", "Taxes", "Old Debt", "OpEx", "MOE"]
	# end: auto-generated types
	pass
