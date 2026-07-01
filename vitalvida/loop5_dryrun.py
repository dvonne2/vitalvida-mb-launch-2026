"""
Loop 5 dry-run — simulates a full earning cycle WITHOUT creating any Bonus
Events or paying anything. Everything runs with dry_run=True.

Run:
    bench --site vitalvida.systemforce.ng execute vitalvida.loop5_dryrun.run
"""

import frappe


def run():
    print("\n===== LOOP 5 DRY-RUN (no money created) =====")

    # DPSR champion dry-run over the current week
    from vitalvida.loop5 import dpsr_champion
    d = dpsr_champion.run_dpsr_champion(dry_run=True)
    print(f"DPSR champion: would emit {d['emitted']} bonus events "
          f"({d['skipped']} skipped) for {d['period_start']}..{d['period_end']}")

    # Payroll dry-run (existing engine already supports dry_run=True)
    from vitalvida.payroll import run_monthly_payroll
    from frappe.utils import today, getdate
    month = str(getdate(today()).replace(day=1))
    res = run_monthly_payroll(month, dry_run=True)
    champ = 0.0
    try:
        from vitalvida.loop5.payroll_seam import preview_champion_bonuses
        # Show champion money that WOULD be added, per employee, read-only
        for emp in frappe.get_all("VV Employee", filters={"is_active": 1},
                                  fields=["name"]):
            p = preview_champion_bonuses(emp.name)
            champ += p.get("Approved", 0.0)
    except Exception as e:
        print(f"  (champion preview skipped: {e})")

    print(f"Payroll dry-run: {res['employee_count']} employees, "
          f"gross={res['total_gross']}, net={res['total_net']}")
    print(f"Approved champion bonuses that WOULD be added next run: {champ}")
    print("No records were created. Dry-run complete.\n")
    return True
