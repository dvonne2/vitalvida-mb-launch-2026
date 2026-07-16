"""Package 15 — versioned schemas + immutable validation evidence.

Validation identity is: schema version + source event identity + PAYLOAD HASH.
That matters: if the payload changes, the identity changes, so a previously
recorded Pass can never be reused to vouch for different content.

This release is audit-only: validation records Pass/Fail evidence and never
blocks or alters the originating transaction. Enforcement requires a separately
designed transaction-boundary package.
"""
import json

import frappe
from frappe.utils import now_datetime

from vitalvida.integration.idempotency import ensure_once, source_key
from vitalvida.schemas import validator



def _active_definition(schema_name, schema_version):
    full = source_key("SCHEMA", schema_name, "v" + str(schema_version))
    name = frappe.db.get_value("Event Schema Definition", {"schema_full_key": full}, "name")
    if not name:
        frappe.throw(f"No schema definition {schema_name} v{schema_version}.")
    return frappe.get_doc("Event Schema Definition", name)


def validate_payload(schema_name, schema_version, source_doctype, source_name, payload):
    """Record immutable validation evidence. Returns the event name.

    A Fail is recorded as immutable evidence and is never raised to block the
    source transaction.
    """
    if not frappe.db.exists(source_doctype, source_name):
        frappe.throw(f"Schema source {source_doctype} {source_name} does not exist; "
                     "validation evidence must reference a real record.")
    definition = _active_definition(schema_name, schema_version)
    schema = json.loads(definition.schema_json)

    problems = validator.validate_schema_definition(schema)
    if problems:
        frappe.throw("Stored schema is not valid for dialect "
                     f"{validator.DIALECT}: {problems}")

    errors = validator.validate_payload(schema, payload)
    payload_hash = validator.stable_hash(payload)

    # identity binds schema version + source identity + payload hash
    key = source_key("SCHV", definition.schema_full_key, source_doctype,
                     source_name, payload_hash)
    res = ensure_once("Schema Validation Event", {"source_key": key}, lambda: {
        "source_key": key, "schema_definition": definition.name,
        "schema_hash": definition.schema_hash, "payload_hash": payload_hash,
        "source_doctype": source_doctype, "source_name": source_name,
        "payload_json": validator.canonical(payload),
        "result": "Pass" if not errors else "Fail",
        "errors_json": json.dumps(errors),
        "validated_at": now_datetime(), "validated_by": frappe.session.user})

    return res["name"]
