"""Package 12 Controls — OBSERVE-ONLY, register-only.

Registers the control evidence events only. It deliberately seeds NO control
definitions until each control is bound to an approved authoritative event
contract. Registers NO consumers: consumer wiring is reintroduced only when
each subscriber has an approved event contract, verified payload schema,
idempotency key, retry behaviour, savepoint boundary, permission model and
authoritative consequence. Event Consumer Map is not touched.
"""
import frappe
from vitalvida.patches._register import register_events

EVENT_DEFINITIONS = [
    dict(event_key="vv.control.executed", event_name="Control Executed", bucket="B",
         authoritative_doctype="Control Execution Event", producer_module="vitalvida.controls",
         erpnext_consequence="None (evidence-only)", policy_ref="CTL-001"),
    dict(event_key="vv.control.exception_opened", event_name="Control Exception Opened", bucket="B",
         authoritative_doctype="Control Exception", producer_module="vitalvida.controls",
         erpnext_consequence="None (evidence-only)", policy_ref="CTL-002"),
    dict(event_key="vv.control.resolved", event_name="Control Resolution Recorded", bucket="B",
         authoritative_doctype="Control Resolution Event", producer_module="vitalvida.controls",
         erpnext_consequence="None (evidence-only)", policy_ref="CTL-003"),
]

# Control definitions are intentionally not seeded. Generic, unbound controls
# would permit evidence against arbitrary source records. Definitions are added
# only with an approved applies_to_event_key contract.
CONTROLS = []


def execute():
    if not frappe.db.exists("Role", "Governance Manager"):
        frappe.get_doc({"doctype": "Role", "role_name": "Governance Manager",
                        "desk_access": 1}).insert(ignore_permissions=True)
    register_events(EVENT_DEFINITIONS)
    for name, version, code, severity, event_key in CONTROLS:
        if frappe.db.exists("Control Definition", {"control_name": name}):
            continue
        frappe.get_doc({"doctype": "Control Definition", "control_name": name,
                        "rule_version": version, "evaluator_code": code,
                        "severity": severity,
                        "applies_to_event_key": event_key}).insert(ignore_permissions=True)
