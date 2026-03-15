
import frappe
from frappe.model.document import Document


class TelesalesAssignmentLog(Document):

	def before_save(self):
		if not self.is_new():
			frappe.throw(
				frappe._("Telesales Assignment Logs are immutable and cannot be edited."),
				frappe.PermissionError
			)

	def on_trash(self):
		frappe.throw(
			frappe._("Telesales Assignment Logs cannot be deleted."),
			frappe.PermissionError
		)
