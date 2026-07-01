"""
Loop 5 bootstrap — create a VV Employee for each active Telesales Closer that
lacks one, so payroll (existing + Loop 5) can attribute money to a rep.

WHY THIS IS A SEPARATE, EXPLICIT SCRIPT (not a migrate patch):
creating payroll identities is money-adjacent. It must be a deliberate operator
action, run once, with visible output — never a silent side effect of install.

Idempotent: re-running skips closers that are already mapped, and it NEVER
edits an employee it did not create. Each record it creates is stamped
created_by_loop5_bootstrap=1 so the FUTURE HR PORTAL (which will own employee
master data, salary, role, employment status, onboarding and payroll identity)
can cleanly adopt or replace these stubs. Loop 5 is not the HR owner; this is a
bridge only.

Sets base_salary = 0 on creation; YOU set the real salary in the UI afterward.

Run (dry-run first):
    bench --site vitalvida.systemforce.ng execute vitalvida.loop5_bootstrap_employees.run --kwargs "{'dry_run': True}"
Then for real:
    bench --site vitalvida.systemforce.ng execute vitalvida.loop5_bootstrap_employees.run
"""

import frappe


def run(dry_run: bool = False) -> dict:
    closers = frappe.get_all("Telesales Closer", filters={"is_active": 1},
                             fields=["name", "closer_name"])
    created, skipped, failed = [], [], []

    for c in closers:
        existing = frappe.db.get_value(
            "VV Employee", {"linked_closer": c.name}, "name")
        if existing:
            skipped.append((c.name, existing))
            continue
        if dry_run:
            created.append((c.name, "(would create)"))
            continue
        try:
            emp = frappe.get_doc({
                "doctype": "VV Employee",
                "employee_name": c.closer_name or c.name,
                "is_active": 1,
                "commission_eligible": 1,
                "linked_closer": c.name,
                "base_salary": 0,
                "created_by_loop5_bootstrap": 1,
            })
            emp.insert(ignore_permissions=True)
            created.append((c.name, emp.name))
        except Exception as e:
            # Most likely a mandatory field on VV Employee we didn't set
            # (e.g. employment_type/staff_role). Report it — do NOT guess a value.
            failed.append((c.name, str(e)))

    if not dry_run and created:
        frappe.db.commit()

    print("\n===== LOOP 5 EMPLOYEE BOOTSTRAP =====")
    print(f"active closers: {len(closers)}")
    print(f"created: {len(created)}  {created}")
    print(f"skipped (already mapped): {len(skipped)}  {skipped}")
    if failed:
        print(f"FAILED: {len(failed)}")
        for name, err in failed:
            print(f"  - {name}: {err}")
        print("  -> a mandatory VV Employee field is missing above. Tell your")
        print("     builder the exact field, or set it, then re-run.")
    if not failed and not dry_run:
        print("\nNEXT: set a real base_salary on each new VV Employee in the UI,")
        print("then run LOOP5-VERIFY (it should now PASS the employee gate).")
    print("=====================================\n")
    return {"created": created, "skipped": skipped, "failed": failed,
            "dry_run": dry_run}
