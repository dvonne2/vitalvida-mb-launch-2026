"""
Loop 4 backfill — create one Customer Profile per distinct canonical phone in VV Order,
then recompute relationship state. Idempotent: re-running enriches, never duplicates.
Reads Loop 1; writes only Loop 4 records. Unresolvable phones are skipped (never faked).

Also seeds the Loop 4 Settings singleton into tabSingles so every engine reads its
configured thresholds (trust_provisional_start=50, min_orders_for_health=2, etc.) from
the first recompute. Without this, get_single_value returns 0 for every unsaved field
and all downstream scores collapse to zero. Idempotent: only fills blank fields.
"""
import frappe


def _seed_loop4_settings():
    """Persist the Loop 4 Settings singleton into tabSingles (fill blanks from defaults)."""
    if not frappe.db.exists("DocType", "Loop 4 Settings"):
        return
    try:
        s = frappe.get_single("Loop 4 Settings")
        meta = frappe.get_meta("Loop 4 Settings")
        for df in meta.fields:
            if df.fieldtype in ("Section Break", "Column Break", "HTML", "Password"):
                continue
            if s.get(df.fieldname) in (None, "") and df.default not in (None, ""):
                s.set(df.fieldname, df.default)
        s.flags.ignore_validate = True
        s.save(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        # settings seeding must never block the backfill; engines fall back to
        # documented defaults via their own guards. Log rather than swallow, so an
        # unexpected seeding failure is diagnosable.
        try:
            frappe.log_error(frappe.get_traceback(), "Loop 4 Settings Seed Failed")
        except Exception:
            pass


def execute():
    if not frappe.db.table_exists("Customer Profile"):
        return
    # seed settings FIRST so engine thresholds read correctly on the first recompute
    _seed_loop4_settings()
    from vitalvida.customer_relationship.identity import normalize_phone, resolve_customer
    rows = frappe.db.sql("""
        SELECT DISTINCT customer_phone, customer_name, customer_email
        FROM `tabVV Order`
        WHERE customer_phone IS NOT NULL AND customer_phone != ''
    """, as_dict=True)
    created = 0; skipped = 0
    for r in rows:
        key = normalize_phone(r["customer_phone"])
        if not key:
            skipped += 1; continue
        if not frappe.db.exists("Customer Profile", key):
            resolve_customer(r["customer_phone"], name=r.get("customer_name"),
                             email=r.get("customer_email"), create=True)
            created += 1
    frappe.db.commit()
    # recompute everyone (bounded — production is small; safe and idempotent)
    from vitalvida.customer_relationship.runner import run_relationship_refresh
    summary = run_relationship_refresh(limit=0)
    try:
        frappe.logger().info(f"[loop4_backfill] created={created} skipped={skipped} {summary}")
    except Exception:
        pass
