"""Source-key idempotency + concurrent-duplicate recovery.

Constitution GOV-004 (custom records idempotent) / CORE-002 (one event once).
The recovery helper closes the race the review flagged: two concurrent callers
both see "no row", both insert, one wins, the loser must return the winner's
row as ``already_emitted`` instead of raising.
"""
import frappe


def source_key(*parts) -> str:
    """Deterministic idempotency key from stable parts."""
    return "::".join("" if p is None else str(p) for p in parts)


def ensure_once(doctype: str, unique_filters: dict, build_values) -> dict:
    """Create ``doctype`` matching ``unique_filters`` exactly once.

    Returns ``{"name": <docname>, "created": bool, "already_emitted": bool}``.
    Safe under concurrency: a losing racer catches the duplicate-insert error,
    re-reads, and returns the surviving row rather than surfacing a failure.
    ``build_values`` may be a dict or a callable returning a dict.
    """
    existing = frappe.db.get_value(doctype, unique_filters, "name")
    if existing:
        return {"name": existing, "created": False, "already_emitted": True}

    values = build_values() if callable(build_values) else dict(build_values)
    values.setdefault("doctype", doctype)
    try:
        doc = frappe.get_doc(values).insert(ignore_permissions=True)
        return {"name": doc.name, "created": True, "already_emitted": False}
    except frappe.exceptions.DuplicateEntryError:
        pass
    except Exception as e:                      # MariaDB 1062 unique-key contention
        if "Duplicate entry" not in str(e) and "1062" not in str(e):
            raise
    # Lost the race: the winner's row now exists -> return it.
    won = frappe.db.get_value(doctype, unique_filters, "name")
    if not won:
        raise
    return {"name": won, "created": False, "already_emitted": True}
