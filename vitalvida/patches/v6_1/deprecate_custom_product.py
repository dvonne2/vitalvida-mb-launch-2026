"""
Package 02 — deprecate the custom `Product` DocType (NON-destructive).

Constitution: ERPNext `Item` is the product authority; the custom `Product`
DocType duplicates it and its `stock_quantity` is a forbidden mutable balance.

VERIFIED (live, commit 0c17568):
    - Product rows                 = 0
    - Product rows w/ nonzero stock = 0
    - Link -> Product fields        = none
    - Custom Link -> Product fields = none
    - `get_doc/get_all/tabProduct/"doctype":"Product"` string refs in app code = none

Because Product is empty AND unreferenced, it is a safe deprecation candidate.
Per the migration rules we DO NOT delete it in this first package. We DEPRECATE:
    1. Assert it is still empty (abort if any row has appeared — data safety).
    2. Hide it from the UI/search via a reversible Property Setter (hidden=1).
Actual DocType removal is deferred to a later package, gated on a fresh live
re-confirmation that Product is still empty and unreferenced.

This patch is idempotent (safe to re-run) and fully reversed by deploy/rollback.sh (which restores the pre-install Property Setter state).
"""

import frappe

DOCTYPE = "Product"
PS_NAME = None  # resolved at runtime


def execute():
    if not frappe.db.exists("DocType", DOCTYPE):
        # Nothing to deprecate; already absent (e.g. removed by a later package).
        frappe.logger("vitalvida.package02").info(
            "deprecate_custom_product: DocType 'Product' absent; nothing to do."
        )
        return

    # 1. DATA SAFETY GATE — never deprecate a table that has grown data.
    rows = frappe.db.count(DOCTYPE)
    if rows:
        frappe.throw(
            f"ABORT: custom '{DOCTYPE}' now has {rows} row(s). Package 02 will not "
            f"deprecate a non-empty Product table. Migrate/verify these rows into "
            f"ERPNext Item first, then re-run.",
            title="Package 02 — Product not empty",
        )

    # 2. Hide from UI/search via a reversible Property Setter (idempotent).
    _set_hidden(1)

    # No explicit frappe.db.commit(): patches run inside the migrate transaction
    # and Frappe commits on success. An explicit commit here would risk a partial
    # write if a later step in the same migrate failed.
    frappe.logger("vitalvida.package02").info(
        "deprecate_custom_product: Product hidden (0 rows, unreferenced)."
    )


def _set_hidden(value):
    """Create/update a Property Setter making the Product DocType hidden.
    make_property_setter is idempotent — re-running updates the same record."""
    frappe.make_property_setter({
        "doctype": DOCTYPE,
        "doctype_or_field": "DocType",
        "property": "hidden",
        "value": value,
        "property_type": "Check",
    }, is_system_generated=False)
