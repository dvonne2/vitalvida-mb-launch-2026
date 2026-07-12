"""
vitalvida/recipe.py  —  Package 02: Product Foundation (Constitution PRD-005)

Single source of truth for expanding a sold package/bundle into its component
ERPNext Items and quantities, resolved from the STRUCTURED recipe
(`Bundle Definition` -> `Bundle Definition Item`, Link -> Item) — never from a
display string such as `vv_package.contents` or `Package.contents`.

Matching convention (identical to the already-in-production
`supply/decision_engine.py`): a sold order carries `package_name`; we match it to
an active `Bundle Definition.bundle_name` after NORMALISATION (case-insensitive,
whitespace-collapsed). If two active Bundle Definitions normalise to the same
name the match is AMBIGUOUS and we resolve to nothing (fail-closed) rather than
silently pick the first — `preflight`/`dryrun` block this condition before it can
reach production.

Purely additive; imports nothing from vitalvida (no circular-import risk).
"""

import re
import frappe
from frappe.utils import cint


def _normalize(name):
    """Lower-case and collapse internal whitespace for robust name matching."""
    return re.sub(r"\s+", " ", str(name or "").strip().lower())


def active_bundles_matching(package_name):
    """Return the list of active Bundle Definition names whose bundle_name
    normalises equal to package_name. Length 0 = unmapped; 1 = unique; >1 =
    ambiguous (a data-quality error preflight/dryrun must block)."""
    if not package_name:
        return []
    target = _normalize(package_name)
    out = []
    for b in frappe.get_all(
        "Bundle Definition", filters={"is_active": 1}, fields=["name", "bundle_name"]
    ):
        if _normalize(b.get("bundle_name")) == target:
            out.append(b["name"])
    return out


def classify(package_name):
    """Classify how a package resolves, so callers can branch explicitly:
        ("structured", [(item, qty), ...])  exactly one active Bundle Definition
        ("empty",      [])                  no active Bundle Definition (caller may fall back)
        ("ambiguous",  [name, name, ...])   >1 active Bundle Definitions collide
    """
    matches = active_bundles_matching(package_name)
    if len(matches) == 1:
        return "structured", _components_of(matches[0])
    if len(matches) == 0:
        return "empty", []
    return "ambiguous", matches


def _components_of(bundle_name):
    """Read the component rows of a Bundle Definition.

    Fail-closed policy: a genuinely missing record (DoesNotExistError) is normal
    for unmapped data and returns []. Any OTHER exception (DB error, programming
    error) is logged and RE-RAISED so callers fail closed rather than silently
    treating a broken read as "no components" (which on a write path would skip a
    deduction). deduct_on_payment's outer handler turns a raised error into a
    logged no-op (payment not blocked); dryrun/verify let it fail the gate."""
    try:
        doc = frappe.get_doc("Bundle Definition", bundle_name)
    except frappe.DoesNotExistError:
        return []
    except Exception:
        frappe.log_error(
            f"PRD-005: unexpected error reading Bundle Definition '{bundle_name}'",
            "Package 02 recipe read error")
        raise
    components = []
    for row in doc.get("products", []):
        if not row.product:
            continue
        components.append((row.product, cint(row.quantity_required)))
    return components


def resolve_components(package_name):
    """Return [(item_code, quantity), ...] for the UNIQUE active Bundle
    Definition matching package_name. Returns [] when unmapped OR ambiguous
    (ambiguity is logged, never silently resolved to a guessed bundle)."""
    status, payload = classify(package_name)
    if status == "structured":
        return payload
    if status == "ambiguous":
        try:
            frappe.log_error(
                f"PRD-005: package '{package_name}' matches multiple active Bundle "
                f"Definitions {payload}; refusing to resolve (fail-closed).",
                "Package 02 ambiguous bundle",
            )
        except Exception:
            pass
    return []


def resolve_components_strict(package_name):
    """resolve_components with non-positive-qty components dropped (write paths)."""
    return [(p, q) for (p, q) in resolve_components(package_name) if q > 0]


def has_structured_recipe(package_name):
    return bool(resolve_components_strict(package_name))


def total_units(package_name):
    """Sum of component quantities. NOTE: not consumed by Package 02 (the COGS
    caller was removed — COGS authority is Finance/FIN-006). Retained as the
    public unit-count helper the Finance package will use when it derives COGS
    from the Stock Ledger."""
    return sum(q for _, q in resolve_components(package_name))


def duplicate_normalized_bundle_names():
    """Return {normalized_name: [bundle names...]} for every group of ACTIVE
    Bundle Definitions that collide after normalisation. Empty dict = clean.
    Used by preflight/dryrun to block ambiguous recipes before install."""
    groups = {}
    for b in frappe.get_all(
        "Bundle Definition", filters={"is_active": 1}, fields=["name", "bundle_name"]
    ):
        groups.setdefault(_normalize(b.get("bundle_name")), []).append(b["name"])
    return {k: v for k, v in groups.items() if len(v) > 1}
