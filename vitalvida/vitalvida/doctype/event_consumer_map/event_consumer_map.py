import frappe
from frappe.model.document import Document


class EventConsumerMap(Document):
    """Child row: one consumer of an event type in the Event Ownership Register."""
    pass
