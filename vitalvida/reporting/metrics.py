"""Governed metric registry — read-only counts over immutable evidence events.

Complements reporting.authoritative (which reads the ERPNext financial
authorities). Nothing here stores a balance; every metric is computed at read
time. Access is role-gated.
"""
import frappe

METRIC_REGISTRY = {
    "control_execution_by_result.v1": {
        "doctype": "Control Execution Event", "group_by": "result"},
    # Derived via NOT EXISTS — there is no cached status to read.
    "open_control_exceptions.v1": {
        "doctype": "Control Exception",
        "not_exists": {"doctype": "Control Resolution Event",
                       "link_field": "control_exception"}},
    "coa_drift_events.v1": {"doctype": "COA Drift Event", "group_by": "company"},
    "schema_validation_by_result.v1": {
        "doctype": "Schema Validation Event", "group_by": "result"},
    # Activation evidence counts only. "Is a consumer live?" is NOT answered here —
    # Event Consumer Map is the single runtime authority for that.
    "consumer_activation_requests.v1": {
        "doctype": "Consumer Activation Request", "group_by": "package_name"},
    "consumer_activations_applied.v1": {
        "doctype": "Consumer Activation Event"},
}


def _require_reader():
    roles = set(frappe.get_roles())
    if not (roles & {"System Manager", "Governance Manager"}):
        frappe.throw("Not permitted to read governance metrics.", frappe.PermissionError)


def metric_registry():
    _require_reader()
    return METRIC_REGISTRY


def run_metric(code):
    _require_reader()
    spec = METRIC_REGISTRY.get(code)
    if not spec:
        frappe.throw("Unknown governed metric")
    dt, group = spec["doctype"], spec.get("group_by")
    if spec.get("not_exists"):
        ne = spec["not_exists"]
        return frappe.db.sql(f"""SELECT COUNT(*) FROM `tab{dt}` x
            WHERE NOT EXISTS (SELECT 1 FROM `tab{ne['doctype']}` y
                               WHERE y.`{ne['link_field']}` = x.name)""")[0][0]
    if group:
        rows = frappe.get_all(dt, fields=[group, "count(name) as count"],
                              group_by=group)
        return {r[group]: r["count"] for r in rows}
    return frappe.db.count(dt, spec.get("filters") or {})


def governance_dashboard():
    _require_reader()
    return {k: run_metric(k) for k in METRIC_REGISTRY}
