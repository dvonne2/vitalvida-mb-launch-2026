"""
DPSR Champion (v5.4).

Reuses dsr.compute_telesales_dsr (dsr_strict = Paid/Assigned x 100). The ladder
gives the FULL bonus for the highest tier reached (60->5k ... 100->100k), NOT a
cumulative sum. To avoid overpaying when a rep's DPSR rises within a period, we
pay only the DELTA between the tier now reached and what was already emitted for
that rep+period. Moving 80%(50k) -> 90%(70k) pays 20k, so the period total is
70k, never 120k. DPSR falling within a period never claws back (delta <= 0).
"""

import frappe
from frappe.utils import today, get_first_day_of_week, add_days

from vitalvida.loop5 import settings as l5s
from vitalvida.loop5 import events as l5e
from vitalvida.loop5 import champions as l5c


def _dpsr_already_emitted(telesales_rep: str, period_start: str) -> float:
    """Sum of non-voided DPSR bonus amounts already emitted for this rep+period."""
    rows = frappe.get_all(
        "Bonus Approval Request",
        filters={"champion_type": l5c.CHAMPION_DPSR,
                 "source_event": ["like", f"dpsr::{telesales_rep}::{period_start}::%"],
                 "l5_voided": ["in", [0, None]]},
        fields=["bonus_amount"],
    )
    return sum(float(r.bonus_amount or 0) for r in rows)


def run_dpsr_champion(period_start: str = None, period_end: str = None,
                      dry_run: bool = False) -> dict:
    from vitalvida.dsr import compute_telesales_dsr

    period_start = period_start or str(get_first_day_of_week(today()))
    period_end = period_end or str(add_days(period_start, 6))

    closers = frappe.get_all("Telesales Closer", filters={"is_active": 1},
                             fields=["name"])
    emitted, skipped = 0, 0

    for c in closers:
        dsr = compute_telesales_dsr(c.name, period_start, period_end)
        tier_full = l5s.dpsr_bonus_for(dsr["dsr_strict"])   # full bonus at tier
        if tier_full <= 0:
            skipped += 1
            continue

        prior = _dpsr_already_emitted(c.name, period_start)
        delta = tier_full - prior
        if delta <= 0:
            # Already at/above this tier for the period (or DPSR fell). No pay.
            skipped += 1
            continue

        source_event = f"dpsr::{c.name}::{period_start}::{int(dsr['dsr_strict'])}"
        if l5c.already_emitted(l5c.CHAMPION_DPSR, source_event):
            skipped += 1
            continue

        if dry_run:
            emitted += 1
            continue

        l5e.emit_business_event(
            l5e.DPSR_MILESTONE, telesales_rep=c.name, value=dsr["dsr_strict"],
            source_ref=source_event,
        )
        res = l5c.emit_bonus_event(
            telesales_rep=c.name, champion_type=l5c.CHAMPION_DPSR,
            amount=delta, source_event=source_event,
            justification=(f"DPSR {dsr['dsr_strict']}% for {period_start} "
                           f"(tier {tier_full:.0f}, prior {prior:.0f}, delta {delta:.0f})"),
        )
        emitted += 1 if res.get("emitted") else 0

    if not dry_run:
        frappe.db.commit()
    return {"period_start": period_start, "period_end": period_end,
            "emitted": emitted, "skipped": skipped, "dry_run": dry_run}
