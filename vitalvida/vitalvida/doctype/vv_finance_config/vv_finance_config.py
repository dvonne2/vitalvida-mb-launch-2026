import frappe
from frappe.model.document import Document


class VVFinanceConfig(Document):
    def validate(self):
        from vitalvida.finance.config import validate_accounts
        problems = validate_accounts(self)
        if problems:
            frappe.msgprint(
                "VV Finance Config warnings:<br>" + "<br>".join(problems),
                title="Account map not yet postable", indicator="orange")
