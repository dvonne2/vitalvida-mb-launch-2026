"""Package 15 Schemas — registers schema validation evidence event; seeds schema
definitions as Draft (immutable once Active). Validation is audit-only; there
is no enforcement switch and no live consumer.
"""
import frappe
from vitalvida.patches._register import register_events
from vitalvida.integration.idempotency import source_key
from vitalvida.schemas import validator

EVENT_DEFINITIONS = [
    dict(event_key="vv.schema.validated", event_name="Schema Validation Recorded", bucket="B",
         authoritative_doctype="Schema Validation Event", producer_module="vitalvida.schemas",
         erpnext_consequence="None (evidence-only)", policy_ref="SCH-001"),
]

SCHEMAS = [
    ("order_closed", "1", {
        "type": "object",
        "required": ["order", "closed_at", "amount"],
        "additionalProperties": False,
        "properties": {
            "order": {"type": "string", "minLength": 1},
            "closed_at": {"type": "string", "minLength": 1},
            "amount": {"type": "number", "minimum": 0},
        }}),
]


def execute():
    register_events(EVENT_DEFINITIONS)
    for name, version, schema in SCHEMAS:
        full = source_key("SCHEMA", name, "v"+version)
        if frappe.db.exists("Event Schema Definition", {"schema_full_key": full}):
            continue
        problems = validator.validate_schema_definition(schema)
        if problems:
            frappe.throw(f"Seeded schema {name} v{version} invalid: {problems}")
        frappe.get_doc({"doctype": "Event Schema Definition", "schema_full_key": full,
                        "schema_name": name, "schema_version": version,
                        "dialect": validator.DIALECT,
                        "schema_json": validator.canonical(schema),
                        "schema_hash": validator.stable_hash(schema),
                        "status": "Draft"}).insert(ignore_permissions=True)
