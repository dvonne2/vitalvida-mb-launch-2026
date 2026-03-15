import frappe
from frappe.model.document import Document


class MessageTemplate(Document):

	def before_save(self):
		self.last_updated_by = frappe.session.user
