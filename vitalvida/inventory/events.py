from __future__ import annotations
import json
import frappe
from frappe.utils import now_datetime


def emit(source_key: str, event_type: str, *, source_doctype: str = "", source_name: str = "",
         payload: dict | None = None):
    existing = frappe.db.get_value("Inventory Custody Event", {"source_key": source_key}, "name")
    if existing:
        return frappe.get_doc("Inventory Custody Event", existing), False
    doc = frappe.get_doc({
        "doctype": "Inventory Custody Event", "source_key": source_key,
        "event_type": event_type, "source_doctype": source_doctype,
        "source_name": source_name, "occurred_at": now_datetime(),
        "status": "Recorded", "payload_json": json.dumps(payload or {}, sort_keys=True, default=str),
    })
    try:
        doc.insert(ignore_permissions=True)
        return doc, True
    except frappe.DuplicateEntryError:
        name = frappe.db.get_value("Inventory Custody Event", {"source_key": source_key}, "name")
        return frappe.get_doc("Inventory Custody Event", name), False


def link_consequence(event_name: str, doctype: str, name: str):
    event = frappe.get_doc("Inventory Custody Event", event_name)
    if event.consequence_name and (event.consequence_doctype != doctype or event.consequence_name != name):
        frappe.throw(f"Event {event_name} already linked to {event.consequence_doctype} {event.consequence_name}.")
    event.db_set({"consequence_doctype": doctype, "consequence_name": name, "status": "Posted"}, update_modified=False)
    return event


def exception(exception_type: str, source_key: str, details: dict):
    name = frappe.db.get_value("Inventory Exception", {"source_key": source_key, "status": "Open"}, "name")
    if name:
        return frappe.get_doc("Inventory Exception", name)
    return frappe.get_doc({"doctype": "Inventory Exception", "exception_type": exception_type,
                           "source_key": source_key, "status": "Open",
                           "details_json": json.dumps(details, sort_keys=True, default=str)}).insert(ignore_permissions=True)
