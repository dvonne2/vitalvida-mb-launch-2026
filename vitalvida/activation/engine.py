"""Consumer activation governance — approval evidence, single runtime authority.

ARCHITECTURE (the point of this module):

    Consumer Activation Request      immutable: what was proposed + change_hash
              |
              v
    Consumer Activation Approval     immutable: who approved that exact hash
              |
              v
    Event Consumer Map  <-- THE SINGLE RUNTIME AUTHORITY
              ^
              |
    Consumer Activation Event        immutable: we applied it, and which row

Nothing here stores "is this consumer active?". That question has exactly one
answer: does the child row exist under the Event Definition. `state()` DERIVES a
view for humans; runtime never calls it and never reads these evidence records.

Approval binds to `change_hash`. If the request were altered after approval the
hash would no longer match and activation fails closed — the thing approved is
provably the thing applied (maker/checker integrity).
"""
import hashlib
import importlib
import json

import frappe
from frappe.utils import now_datetime

from vitalvida.governance.immutable import require_distinct_users
from vitalvida.integration.idempotency import ensure_once, source_key

REQUEST = "Consumer Activation Request"
APPROVAL = "Consumer Activation Approval Event"
ACTIVATION = "Consumer Activation Event"
REVERSAL = "Consumer Activation Reversal Event"


_GOVERNANCE_ROLES = {"Governance Manager", "System Manager"}

def _require_governance_role(action):
    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection(_GOVERNANCE_ROLES):
        frappe.throw(
            f"Only Governance Manager or System Manager may {action} consumer wiring.",
            frappe.PermissionError)

def _assert_subscriber_exists(module_path, method_name):
    try:
        module = importlib.import_module(module_path)
    except Exception as exc:
        frappe.throw(f"Consumer module {module_path!r} is not importable: {exc}")
    if not callable(getattr(module, method_name, None)):
        frappe.throw(f"Consumer method {module_path}.{method_name} does not exist or is not callable.")

_CHANGE_FIELDS = ("parent_event_key", "proposed_consumer_module",
                  "proposed_consumer_method", "proposed_read_mode",
                  "proposed_delivery")


def _canonical(values: dict) -> str:
    return json.dumps({k: values.get(k) for k in _CHANGE_FIELDS},
                      sort_keys=True, separators=(",", ":"), default=str)


def compute_change_hash(values: dict) -> str:
    return hashlib.sha256(_canonical(values).encode()).hexdigest()


# --------------------------------------------------------------- request
def request_consumer(spec) -> str:
    """Record an immutable request to wire a consumer. Idempotent. Applies nothing."""
    _require_governance_role("request")
    _assert_subscriber_exists(spec["proposed_consumer_module"],
                              spec["proposed_consumer_method"])
    key = source_key("CAR", spec["package_name"], spec["parent_event_key"],
                     spec["proposed_consumer_method"])
    change_hash = compute_change_hash(spec)
    res = ensure_once(REQUEST, {"request_key": key}, lambda: {
        "request_key": key,
        "package_name": spec["package_name"],
        "parent_event_key": spec["parent_event_key"],
        "proposed_consumer_module": spec["proposed_consumer_module"],
        "proposed_consumer_method": spec["proposed_consumer_method"],
        "proposed_read_mode": spec["proposed_read_mode"],
        "proposed_delivery": spec["proposed_delivery"],
        "authoritative_source_doctype": spec["authoritative_source_doctype"],
        "authoritative_source_description": spec.get("authoritative_source_description"),
        "authoritative_consequence": spec.get("authoritative_consequence"),
        "justification": spec.get("justification") or "Registered by package install; inert pending approval.",
        "change_hash": change_hash,
        "requested_by": frappe.session.user,
        "requested_at": now_datetime(),
    })
    return res["name"]


# --------------------------------------------------------------- approve
def approve(request_name, notes=None) -> str:
    """Approve a request. Approver must differ from requester (server-side SoD)."""
    _require_governance_role("approve")
    req = frappe.get_doc(REQUEST, request_name)
    require_distinct_users(req.requested_by, frappe.session.user, "approve")
    if _latest(REVERSAL, request_name):
        frappe.throw("This request was reversed; raise a new request.")
    key = source_key("CAA", req.request_key, req.change_hash)
    res = ensure_once(APPROVAL, {"source_key": key}, lambda: {
        "source_key": key, "activation_request": req.name,
        "approved_change_hash": req.change_hash,
        "approved_by": frappe.session.user, "approved_at": now_datetime(),
        "notes": notes})
    return res["name"]


# -------------------------------------------------------------- activate
def activate(request_name) -> str:
    """Apply the approved consumer to Event Consumer Map — the single authority.

    Fails closed unless an approval exists for the request's CURRENT change_hash.
    Idempotent. A conflicting existing consumer (same method, different
    read_mode/delivery) fails closed rather than being silently overwritten.
    """
    _require_governance_role("activate")
    req = frappe.get_doc(REQUEST, request_name)
    _assert_subscriber_exists(req.proposed_consumer_module,
                              req.proposed_consumer_method)
    approval = frappe.db.get_value(
        APPROVAL, {"activation_request": req.name,
                   "approved_change_hash": req.change_hash}, "name")
    if not approval:
        frappe.throw("No approval matching this request's change_hash. "
                     "Unapproved (or altered) changes cannot be activated.")
    if _latest(REVERSAL, req.name):
        frappe.throw("This request was reversed; raise a new request.")

    ed_name = frappe.db.get_value("Event Definition",
                                  {"event_key": req.parent_event_key}, "name")
    if not ed_name:
        frappe.throw(f"No Event Definition for {req.parent_event_key!r}.")
    ed = frappe.get_doc("Event Definition", ed_name)

    for r in (ed.get("consumers") or []):
        if r.get("consumer_method") == req.proposed_consumer_method:
            if (r.get("read_mode") == req.proposed_read_mode
                    and r.get("delivery") == req.proposed_delivery):
                return _record_activation(req, r.name)      # idempotent
            frappe.throw(
                f"{req.proposed_consumer_method} is already wired on "
                f"{req.parent_event_key} with different read_mode/delivery "
                f"({r.get('read_mode')}/{r.get('delivery')}); refusing to activate.")

    child = ed.append("consumers", {
        "consumer_module": req.proposed_consumer_module,
        "consumer_method": req.proposed_consumer_method,
        "read_mode": req.proposed_read_mode,
        "delivery": req.proposed_delivery})
    ed.save(ignore_permissions=True)
    return _record_activation(req, child.name)


def _record_activation(req, child_row) -> str:
    key = source_key("CAE", req.request_key, req.change_hash)
    res = ensure_once(ACTIVATION, {"source_key": key}, lambda: {
        "source_key": key, "activation_request": req.name,
        "applied_change_hash": req.change_hash, "applied_child_row": child_row,
        "activated_by": frappe.session.user, "activated_at": now_datetime()})
    return res["name"]


# --------------------------------------------------------------- reverse
def reverse(request_name, reason) -> str:
    """Remove ONLY the child row this request created; append reversal evidence.

    History is never rewritten: the request, its approval and its activation all
    remain. Reversal is a new immutable record (rule 8).
    """
    _require_governance_role("reverse")
    if not reason:
        frappe.throw("A reversal reason is required.")
    req = frappe.get_doc(REQUEST, request_name)
    act_name = _latest(ACTIVATION, req.name)
    if not act_name:
        frappe.throw("Nothing to reverse: this request was never activated.")
    act = frappe.get_doc(ACTIVATION, act_name)

    removed = None
    ed_name = frappe.db.get_value("Event Definition",
                                  {"event_key": req.parent_event_key}, "name")
    if ed_name:
        ed = frappe.get_doc("Event Definition", ed_name)
        keep = [r for r in (ed.get("consumers") or []) if r.name != act.applied_child_row]
        if len(keep) != len(ed.get("consumers") or []):
            removed = act.applied_child_row
            ed.set("consumers", keep)
            ed.save(ignore_permissions=True)

    key = source_key("CAR-REV", req.request_key, act.name)
    res = ensure_once(REVERSAL, {"source_key": key}, lambda: {
        "source_key": key, "activation_request": req.name,
        "consumer_activation_event": act.name, "removed_child_row": removed,
        "reason": reason, "reversed_by": frappe.session.user,
        "reversed_at": now_datetime()})
    return res["name"]


def reverse_all_activated(reason="rollback") -> int:
    n = 0
    for name in frappe.get_all(REQUEST, pluck="name"):
        if _latest(ACTIVATION, name) and not _latest(REVERSAL, name):
            reverse(name, reason)
            n += 1
    return n


# ----------------------------------------------------------------- state
def _latest(doctype, request_name):
    rows = frappe.get_all(doctype, filters={"activation_request": request_name},
                          order_by="creation desc", limit=1, pluck="name")
    return rows[0] if rows else None


def state(request_name) -> dict:
    """DERIVED read-only view. Never stored, never consulted at runtime.

    `live` is answered by Event Consumer Map alone — the single authority.
    """
    req = frappe.get_doc(REQUEST, request_name)
    approved = bool(frappe.db.get_value(
        APPROVAL, {"activation_request": req.name,
                   "approved_change_hash": req.change_hash}, "name"))
    activated = _latest(ACTIVATION, req.name)
    reversed_ = _latest(REVERSAL, req.name)

    ed_name = frappe.db.get_value("Event Definition",
                                  {"event_key": req.parent_event_key}, "name")
    live = False
    if ed_name:
        live = bool(frappe.get_all(
            "Event Consumer Map",
            filters={"parenttype": "Event Definition", "parent": ed_name,
                     "consumer_method": req.proposed_consumer_method}, limit=1))
    if reversed_:
        status = "Reversed"
    elif activated:
        status = "Activated"
    elif approved:
        status = "Approved"
    else:
        status = "Requested"
    return {"request": req.name, "status": status, "approved": approved,
            "live_in_event_consumer_map": live, "change_hash": req.change_hash}
