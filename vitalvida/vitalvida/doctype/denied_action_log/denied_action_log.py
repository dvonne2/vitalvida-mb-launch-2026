import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class DeniedActionLog(Document):
	def before_insert(self):
		"""Stamp who attempted the denied action and when."""
		if not self.attempted_by:
			self.attempted_by = frappe.session.user
		if not self.attempted_at:
			self.attempted_at = now_datetime()

	def before_save(self):
		"""Audit rows are immutable once written."""
		if self.is_new():
			return
		frappe.throw(
			frappe._("Denied Action Log entries are immutable and cannot be edited."),
			frappe.PermissionError,
		)

	def on_trash(self):
		frappe.throw(
			frappe._("Denied Action Log entries cannot be deleted."),
			frappe.PermissionError,
		)
