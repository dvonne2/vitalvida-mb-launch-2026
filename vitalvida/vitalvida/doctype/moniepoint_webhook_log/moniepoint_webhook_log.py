"""
Moniepoint Webhook Log DocType Controller — M6
Immutable — no edits, no deletes after creation.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class MoniepointWebhookLog(Document):

	def before_insert(self):
		"""Auto-generate log_id if not set."""
		if not self.log_id:
			import uuid
			self.log_id = str(uuid.uuid4())

	def before_save(self):
		"""Block all edits after creation — this DocType is immutable."""
		if not self.is_new():
			frappe.throw(
				_("Moniepoint Webhook Logs are immutable and cannot be edited."),
				frappe.PermissionError
			)

	def on_trash(self):
		"""Block deletion."""
		frappe.throw(
			_("Moniepoint Webhook Logs cannot be deleted."),
			frappe.PermissionError
		)
