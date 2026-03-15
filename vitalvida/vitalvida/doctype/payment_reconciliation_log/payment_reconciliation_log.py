"""
Payment Reconciliation Log DocType Controller — M11

Immutable audit trail — records written by reconciliation.py only.
Finance can view all records and update reconciliation_status only.
No record can ever be deleted.
"""
import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class PaymentReconciliationLog(Document):

	def before_save(self):
		"""Block all edits except reconciliation_status change by Finance."""
		if self.is_new():
			return  # Allow initial insert by reconciliation.py

		# Only allow reconciliation_status to change
		doc_before = self.get_doc_before_save()
		if not doc_before:
			return

		protected = [
			"webhook", "order", "match_tier", "match_confidence",
			"amount_received", "amount_expected", "amount_delta", "notes"
		]
		for field in protected:
			if self.get(field) != doc_before.get(field):
				frappe.throw(
					frappe._(f"Field '{field}' is immutable and cannot be changed."),
					frappe.PermissionError
				)

	def on_update(self):
		"""
		M11: Finance sets Manually Confirmed → trigger _mark_order_paid().
		"""
		doc_before = self.get_doc_before_save()
		if not doc_before:
			return

		prev_status = doc_before.reconciliation_status
		curr_status = self.reconciliation_status

		if curr_status == "Manually Confirmed" and prev_status != "Manually Confirmed":
			if not self.order:
				frappe.log_error(
					f"Manually Confirmed set on PRL {self.name} but no order linked.",
					"M11 Manual Confirm No Order"
				)
				return

			try:
				from vitalvida.reconciliation import _mark_order_paid
				_mark_order_paid(self.order)

				# Stamp who confirmed and when
				frappe.db.set_value("Payment Reconciliation Log", self.name, {
					"reconciled_by": frappe.session.user,
					"reconciled_at": now_datetime(),
				})
				frappe.db.commit()

			except Exception as e:
				frappe.log_error(str(e), "M11 Manual Confirm Error")

	def on_trash(self):
		frappe.throw(
			frappe._("Payment Reconciliation Logs cannot be deleted."),
			frappe.PermissionError
		)
