"""Shared idempotent Event Definition registration for Packages 12-16.

Registers the event TYPE (one row per event_key) with is_active=1 and appends
NO consumer child rows. This release registers no consumers at all; Event
Consumer Map is never touched by any install patch.
"""
import frappe


def register_events(event_definitions):
    if not frappe.db.exists("DocType", "Event Definition"):
        frappe.throw("Package 01 Event Definition missing; install the spine first.")
    meta = frappe.get_meta("Event Definition")
    for d in event_definitions:
        if frappe.db.exists("Event Definition", {"event_key": d["event_key"]}):
            continue
        row = {"doctype": "Event Definition", "is_active": 1}
        row.update({k: v for k, v in d.items() if meta.has_field(k)})
        frappe.get_doc(row).insert(ignore_permissions=True)


def request_consumers(specs):
    """Record immutable, inert consumer-wiring REQUESTS. Applies nothing.

    Event Consumer Map stays untouched until a request is approved by a
    different user and explicitly activated.
    """
    from vitalvida.activation.engine import request_consumer
    for spec in specs:
        request_consumer(spec)
