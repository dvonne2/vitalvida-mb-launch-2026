import frappe
from frappe.model.document import Document

class DADistanceMatrix(Document):
    def validate(self):
        if self.from_da == self.to_da:
            frappe.throw("From DA and To DA cannot be the same.")
