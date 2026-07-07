"""
Loop 5 verification — READ-ONLY. Confirms the install is wired correctly
WITHOUT paying anything or mutating data.

Run:
    bench --site vitalvida.systemforce.ng execute vitalvida.loop5_verify.run
(or paste into `bench --site ... console`)

Exit summary prints PASS/FAIL per check.
"""

import frappe


def run():
    checks = []

    def ok(name, cond, detail=""):
        checks.append((name, bool(cond), detail))

    # 1. New doctypes exist
    for dt in ["Revenue Business Event", "Upsell Event", "Commercial Change Log",
               "Customer Revival State", "Loop 5 Settings"]:
        ok(f"DocType present: {dt}", frappe.db.exists("DocType", dt))

    # 2. Reused infra still present (must NOT be duplicated)
    for dt in ["Bonus Approval Request", "VV Commission Settings",
               "Monthly Payroll Run", "DSR Snapshot"]:
        ok(f"Reused DocType intact: {dt}", frappe.db.exists("DocType", dt))

    # 3. Custom fields added
    for dt, fn in [("VV Order", "is_upsold"), ("VV Order", "original_value"),
                   ("Bonus Approval Request", "champion_type"),
                   ("Bonus Approval Request", "l5_paid"),
                   ("Bonus Approval Request", "l5_voided")]:
        ok(f"Custom field {dt}.{fn}",
           frappe.db.exists("Custom Field", {"dt": dt, "fieldname": fn}))

    # 4. Ladders seeded
    try:
        from vitalvida.loop5 import settings as l5s
        ok("Upsell commission configured", l5s.upsell_commission_amount() > 0,
           f"amount={l5s.upsell_commission_amount()}")
        ok("DPSR ladder present", len(l5s.get('dpsr_ladder') or []) >= 5)
    except Exception as e:
        ok("Settings readable", False, str(e))

    # 5. Payroll seam importable (does not run payroll)
    try:
        from vitalvida.loop5.payroll_seam import compute_champion_bonuses  # noqa
        ok("Payroll seam importable", True)
    except Exception as e:
        ok("Payroll seam importable", False, str(e))

    # 5b. CRITICAL: every active closer must map to a VV Employee, or Loop 5
    #     pays nobody. This is a data-readiness gate, not a code check.
    try:
        from vitalvida.loop5.champions import unmapped_active_closers
        unmapped = unmapped_active_closers()
        total_emp = frappe.db.count("VV Employee", {"is_active": 1})
        ok(f"VV Employee records exist (found {total_emp})", total_emp > 0,
           "payroll pays NOBODY with 0 employees" if total_emp == 0 else "")
        ok("All active closers mapped to an employee",
           len(unmapped) == 0,
           f"UNMAPPED (will earn nothing): {unmapped}" if unmapped else "")
    except Exception as e:
        ok("Employee mapping check", False, str(e))

    # 6. Reused approval spine importable
    try:
        from vitalvida.telesales_scoring import calculate_bonus  # noqa
        from vitalvida.dsr import compute_telesales_dsr  # noqa
        ok("Approval + DPSR spine importable", True)
    except Exception as e:
        ok("Approval + DPSR spine importable", False, str(e))

    passed = sum(1 for _, c, _ in checks if c)
    print("\n===== LOOP 5 VERIFY =====")
    for name, cond, detail in checks:
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    print(f"-------------------------\n{passed}/{len(checks)} checks passed\n")
    return passed == len(checks)
