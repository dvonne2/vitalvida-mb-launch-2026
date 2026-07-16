"""Package 12 Controls — OBSERVE-ONLY. Detect, record, never enforce.

This package has no enforcement path and no mode switch. A failing control
records immutable execution evidence and an immutable Control Exception; it
never blocks or alters the originating transaction. There is deliberately no
"Active" mode: blocking controls need explicit transaction boundaries, rollback
behaviour, override governance, an availability policy and user-facing
remediation — all of which belong to a separately designed enforcement package.

Every execution is immutable evidence, idempotent via the spine's
source_key/ensure_once. Separation of duties on resolution is server-side.
"""
import frappe
from frappe.utils import now_datetime
from vitalvida.integration.idempotency import source_key, ensure_once
from vitalvida.integration import registry as spine_registry
from vitalvida.governance.hashing import canonical, stable_hash
from vitalvida.governance.immutable import require_distinct_users
from vitalvida.controls import registry


def evaluate_control(control_name, source_doctype, source_name, inputs=None):
    """Run a control's handler and record an immutable Control Execution Event.

    Records evidence only. A Fail records an exception; it NEVER raises to block
    the source transaction. This package is observe-only by design.
    """
    control = frappe.get_doc("Control Definition", control_name)
    inputs = inputs or {}

    # ---- verify the source is real and authoritative (fail closed) ----
    if not frappe.db.exists(source_doctype, source_name):
        frappe.throw(f"Control source {source_doctype} {source_name} does not exist; "
                     "controls record evidence against real records only.")
    if control.applies_to_event_key:
        # the spine refuses any doctype that is not the registered authority for
        # this event — a control may not be recorded against a non-authoritative
        # source (CORE-002 one event / one owner).
        spine_registry.assert_authorized_emitter(control.applies_to_event_key,
                                                 source_doctype)

    # ---- identity includes the INPUT HASH ----
    # Without it, re-evaluating the same record with different inputs would reuse
    # the earlier key and be silently skipped by ensure_once, losing evidence.
    input_hash = stable_hash(inputs)
    key = source_key("CTL", control.name, source_doctype, source_name,
                     "v"+str(control.rule_version), input_hash)
    # SECURITY: resolve the evaluator from the source-controlled registry by code.
    # Never frappe.get_attr() a value read from the database.
    config = frappe.parse_json(control.evaluator_config or "{}")
    result, message = registry.get(control.evaluator_code)(source_doctype, source_name, config)
    res = ensure_once("Control Execution Event", {"source_key": key}, lambda: {
        "source_key": key, "control_definition": control.name,
        "rule_version": control.rule_version, "source_doctype": source_doctype,
        "source_name": source_name,
        "input_json": canonical(inputs), "input_hash": input_hash,
        "result": result, "message": message,
        "evaluated_at": now_datetime(), "evaluated_by": frappe.session.user})
    if res["created"] and result == "Fail":
        _open_exception(res["name"], message)
    return res["name"]


def _open_exception(execution_event, reason):
    key = source_key("CTLX", execution_event)
    ev = frappe.get_doc("Control Execution Event", execution_event)
    ensure_once("Control Exception", {"source_key": key}, lambda: {
        "source_key": key, "control_execution_event": execution_event,
        "control_definition": ev.control_definition,
        "source_doctype": ev.source_doctype, "source_name": ev.source_name,
        "reason": reason,
        "opened_at": now_datetime(), "opened_by": frappe.session.user})


def resolve_exception(exception_name, resolution, evidence_reference=None):
    exc = frappe.get_doc("Control Exception", exception_name)
    require_distinct_users(exc.opened_by, frappe.session.user, "resolve")
    # Identity includes the resolution CONTENT hash, for the same reason the
    # execution key includes the input hash: without it a corrected re-resolution
    # would reuse the earlier key and be silently dropped by ensure_once.
    resolution_hash = stable_hash({"resolution": resolution,
                                   "evidence_reference": evidence_reference})
    key = source_key("CTLR", exception_name, resolution_hash)
    res = ensure_once("Control Resolution Event", {"source_key": key}, lambda: {
        "source_key": key, "control_exception": exception_name,
        "resolution": resolution, "evidence_reference": evidence_reference,
        "resolution_hash": resolution_hash,
        "resolved_at": now_datetime(), "resolved_by": frappe.session.user})
    return res["name"]


# ------------------------------------------------------------------ derived
# Resolution state is NEVER stored on the exception. It is derived from the
# existence of an immutable Control Resolution Event referencing it.

def resolution_of(exception_name):
    """The authoritative resolution event for an exception, or None."""
    rows = frappe.get_all("Control Resolution Event",
                          filters={"control_exception": exception_name},
                          fields=["name", "resolved_at", "resolved_by", "resolution"],
                          order_by="creation asc", limit=1)
    return rows[0] if rows else None


def is_resolved(exception_name) -> bool:
    return bool(resolution_of(exception_name))


def exception_state(exception_name) -> dict:
    r = resolution_of(exception_name)
    return {"exception": exception_name,
            "status": "Resolved" if r else "Open",
            "resolved_at": r["resolved_at"] if r else None,
            "resolved_by": r["resolved_by"] if r else None,
            "resolution_event": r["name"] if r else None}


def open_exceptions(limit=100):
    """Open == no Control Resolution Event references it. Derived, never cached."""
    return frappe.db.sql("""
        SELECT x.name, x.control_definition, x.source_doctype, x.source_name,
               x.reason, x.opened_at, x.opened_by
          FROM `tabControl Exception` x
         WHERE NOT EXISTS (SELECT 1 FROM `tabControl Resolution Event` r
                            WHERE r.control_exception = x.name)
         ORDER BY x.opened_at DESC LIMIT %s""", (int(limit),), as_dict=True)
