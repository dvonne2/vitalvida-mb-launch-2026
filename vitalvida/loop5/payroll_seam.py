"""
Payroll seam (v5.4) — the ONLY money Loop 5 feeds into payroll.

SAFETY: a payroll DRY-RUN must never mutate anything. Therefore:
  * `mark_paid` DEFAULTS TO FALSE. Marking bonuses paid is opt-in, and only the
    real (persisting) payroll run passes mark_paid=True.
  * `preview_amount()` is the read-only entry point the dry-run path uses.
The real run marks paid under a row lock so a bonus can never pay twice.
"""

import frappe

# adjusted_amount is a STANDARD field on Bonus Approval Request (confirmed in the
# source recon), but we detect it at runtime so the seam degrades gracefully to
# bonus_amount if any environment lacks it — a preview must never crash payroll.
def _has_adjusted() -> bool:
    try:
        return frappe.get_meta("Bonus Approval Request").has_field("adjusted_amount")
    except Exception:
        return False


def _approved_unpaid_rows(employee: str, for_update: bool):
    lock = "FOR UPDATE" if for_update else ""
    adj = "adjusted_amount" if _has_adjusted() else "NULL AS adjusted_amount"
    return frappe.db.sql(
        f"""
        SELECT name, bonus_amount, {adj}
        FROM `tabBonus Approval Request`
        WHERE employee = %s
          AND status = 'Approved'
          AND champion_type IS NOT NULL AND champion_type != ''
          AND (l5_paid = 0 OR l5_paid IS NULL)
          AND (l5_voided = 0 OR l5_voided IS NULL)
        {lock}
        """,
        (employee,), as_dict=True,
    )


def _sum(rows):
    total = 0.0
    for r in rows:
        total += float(r.adjusted_amount if r.adjusted_amount not in (None, 0)
                       else r.bonus_amount or 0)
    return round(total, 2)


def preview_amount(employee: str) -> float:
    """READ-ONLY sum of approved, unpaid, unvoided champion bonuses.
    No lock, no writes. This is what payroll dry-run must use."""
    if not employee:
        return 0.0
    return _sum(_approved_unpaid_rows(employee, for_update=False))


def compute_champion_bonuses(emp, month_start: str, month_end: str,
                             mark_paid: bool = False) -> float:
    """Return the champion-bonus total for one employee.

    mark_paid defaults to FALSE — safe for dry-run. Only a REAL payroll run
    passes mark_paid=True, which locks the rows and flags them paid atomically
    so they can never pay twice. If mark_paid is False, this is pure read.
    """
    employee = emp["name"] if isinstance(emp, dict) else emp
    if not employee:
        return 0.0

    if not mark_paid:
        # Read-only path — identical math, zero mutation. Used by dry-run.
        return preview_amount(employee)

    # Real run: lock, sum, mark paid in one atomic transaction.
    rows = _approved_unpaid_rows(employee, for_update=True)
    total = _sum(rows)
    names = [r.name for r in rows]
    if names:
        frappe.db.sql(
            "UPDATE `tabBonus Approval Request` SET l5_paid = 1 WHERE name IN ({})".format(
                ", ".join(["%s"] * len(names))),
            tuple(names),
        )
    return total


def preview_champion_bonuses(employee: str) -> dict:
    """READ-ONLY breakdown for the Performance & Earnings dashboard."""
    if not employee:
        return {"Approved": 0.0, "Pending": 0.0, "Paid": 0.0, "by_champion": {}}
    fields = ["champion_type", "status", "bonus_amount", "l5_paid", "l5_voided"]
    if _has_adjusted():
        fields.insert(3, "adjusted_amount")
    rows = frappe.get_all(
        "Bonus Approval Request",
        filters={"employee": employee, "champion_type": ["is", "set"]},
        fields=fields,
    )
    out = {"Approved": 0.0, "Pending": 0.0, "Paid": 0.0, "by_champion": {}}
    for r in rows:
        adj = r.get("adjusted_amount")
        amt = float(adj if adj not in (None, 0) else r.bonus_amount or 0)
        if r.get("l5_voided"):
            continue
        if r.l5_paid:
            out["Paid"] += amt
        elif r.status == "Approved":
            out["Approved"] += amt
        elif r.status == "Pending":
            out["Pending"] += amt
        out["by_champion"].setdefault(r.champion_type, 0.0)
        out["by_champion"][r.champion_type] += amt
    return out


def settle_champion_bonuses(employee: str, payroll_run: str = None) -> float:
    """Mark this employee's approved, unpaid, unvoided champion bonuses as paid.

    Call this ONLY after the Monthly Payroll Run has been successfully inserted,
    and ONLY on a real (non-dry-run) payroll. Locks rows so a bonus can never be
    settled twice. Returns the total settled (should match the payslip preview).
    """
    if not employee:
        return 0.0
    rows = _approved_unpaid_rows(employee, for_update=True)
    total = _sum(rows)
    names = [r.name for r in rows]
    if names:
        frappe.db.sql(
            "UPDATE `tabBonus Approval Request` SET l5_paid = 1 WHERE name IN ({})".format(
                ", ".join(["%s"] * len(names))),
            tuple(names),
        )
    return total
