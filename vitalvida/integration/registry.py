"""Read the Event Ownership Register and enforce authorised emission.

CORE-002: an event has exactly one owner. ``assert_authorized_emitter`` refuses
to let a module raise a consequence for an event it does not own, which is how
Package 01 prevents a second writer from re-appearing.
"""
import frappe


def get_definition(event_key: str) -> dict:
    name = frappe.db.exists("Event Definition", {"event_key": event_key})
    if not name:
        frappe.throw(f"No Event Definition registered for {event_key!r}.")
    return frappe.get_doc("Event Definition", name).as_dict()


def get_owner(event_key: str) -> str:
    return get_definition(event_key).get("authoritative_doctype")


def list_events(bucket: str | None = None) -> list:
    filters = {"is_active": 1}
    if bucket:
        filters["bucket"] = bucket
    return frappe.get_all("Event Definition", filters=filters,
                          fields=["event_key", "event_name", "bucket",
                                  "authoritative_doctype", "erpnext_consequence",
                                  "policy_ref"])


def assert_authorized_emitter(event_key: str, source_doctype: str):
    """Raise unless ``source_doctype`` is the registered authority for the event."""
    owner = get_owner(event_key)
    if owner and source_doctype != owner:
        frappe.throw(
            f"{source_doctype} is not authorised to emit {event_key}; the "
            f"authoritative record is {owner} (Constitution CORE-002).")
