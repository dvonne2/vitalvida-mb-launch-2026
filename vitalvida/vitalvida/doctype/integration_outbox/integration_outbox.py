import frappe
from frappe.model.document import Document


class IntegrationOutbox(Document):
    """Async delivery job for a consequence/consumer of an already-recorded fact.

    An outbox row is an operational delivery record, not a business fact: it
    points at the authoritative record (source_doctype+source_name) and never
    copies its payload. Dedupe is on (event_key, source_name, consumer_method)
    so re-enqueue is idempotent.
    """
    pass
