"""
Loop 4 — Relationship refresh runner. The scheduled (but SHIPPED-OFF) job that
recomputes relationship state across customers. Run manually for the dry-run.
"""
import frappe


def run_relationship_refresh(limit=0):
    """Recompute profiles for all (or first `limit`) Customer Profiles. Idempotent."""
    from vitalvida.customer_relationship.profile import recompute_profile
    names = [p.name for p in frappe.get_all("Customer Profile", fields=["name"],
             limit=limit or None)]
    done = 0; errors = 0
    for n in names:
        try:
            recompute_profile(n); done += 1
        except Exception as e:
            errors += 1
            frappe.log_error(f"recompute_profile {n}: {e}", "Loop4 Runner")
    frappe.db.commit()
    return {"profiles": len(names), "recomputed": done, "errors": errors}
