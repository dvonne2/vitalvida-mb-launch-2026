# Copyright (c) 2026, azeez and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class Product(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		category: DF.Data | None
		description: DF.Text | None
		product_name: DF.Data
		sku: DF.Data | None
		stock_quantity: DF.Int
		unit_price: DF.Currency
	# end: auto-generated types
	pass
