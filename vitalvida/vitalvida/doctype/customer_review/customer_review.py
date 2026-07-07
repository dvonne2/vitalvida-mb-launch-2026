import frappe
from frappe.model.document import Document


class CustomerReview(Document):
    def validate(self):
        # derive sentiment from rating if not set
        if self.rating and not self.sentiment:
            r = int(self.rating)
            self.sentiment = "Positive" if r >= 4 else ("Neutral" if r == 3 else "Negative")
