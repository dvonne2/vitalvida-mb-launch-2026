# Copyright (c) 2026, azeez and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class VitalVidaSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF
		from vitalvida.vitalvida.doctype.delivery_fee_table.delivery_fee_table import DeliveryFeeTable

		commitment_fee_amount: DF.Currency
		commitment_refund_orders: DF.Int
		delivery_fee_table: DF.Table[DeliveryFeeTable]
		inventory_report_email: DF.Data | None
		max_active_buyers: DF.Int
		max_da_pickup_transport: DF.Currency
		max_storekeeper_fee: DF.Currency
		mini_whale_sla_hours: DF.Int
		mini_whale_threshold: DF.Currency
		ops_alert_emails: DF.SmallText | None
		performance_weight_lead_share: DF.Float
		performance_weight_top_percent: DF.Float
		stock_movement_sla_hours: DF.Int
		telesales_assignment_mode: DF.Literal["Round Robin", "Performance Weighted"]
		variance_critical_percent: DF.Float
		variance_tolerance_percent: DF.Float
		variance_warning_percent: DF.Float
		webhook_secret: DF.Data | None
		whale_sla_hours: DF.Int
		whale_threshold: DF.Currency
		zero_weeks_suspend_threshold: DF.Int
	# end: auto-generated types

	pass
