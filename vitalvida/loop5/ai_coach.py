"""
AI Sales Coach — READ-ONLY.

CONSTITUTIONAL: recommends only. It must never award commission, approve
bonuses, edit orders, or change earnings. Every function here is a pure read
that returns suggestions. No .insert / .save / .db_set anywhere in this module.
"""

import frappe
from frappe.utils import add_days, today

from vitalvida.loop5 import settings as l5s


def coach_for_rep(telesales_rep: str) -> dict:
    """Return prioritised, read-only recommendations for one rep."""
    recs = []

    # Orders that can still be upsold (Confirmed/Assigned, not yet paid, no upsell)
    upsellable = frappe.db.count("VV Order", {
        "telesales_rep": telesales_rep,
        "order_status": ["in", ["Confirmed", "Assigned", "Out for Delivery"]],
    })
    if upsellable:
        recs.append({"type": "upsell",
                     "message": f"{upsellable} live orders could be upsold before assignment."})

    # Dormant customers (candidates for revival) — read-only heuristic
    window = l5s.revival_dormancy_days()
    dormant = frappe.db.sql(
        """
        SELECT COUNT(DISTINCT customer_phone) AS c
        FROM `tabVV Order`
        WHERE telesales_rep = %s AND order_status = 'Paid'
          AND paid_at < %s
        """,
        (telesales_rep, add_days(today(), -window)), as_dict=True,
    )
    dormant_count = dormant[0].c if dormant else 0
    if dormant_count:
        recs.append({"type": "revival",
                     "message": f"{dormant_count} past customers are dormant {window}+ days — candidates to revive."})

    # DPSR risk
    from vitalvida.dsr import compute_telesales_dsr
    from frappe.utils import get_first_day_of_week
    ws = str(get_first_day_of_week(today()))
    dsr = compute_telesales_dsr(telesales_rep, ws, str(add_days(ws, 6)))
    if dsr["dsr_strict"] < 80:
        recs.append({"type": "dpsr",
                     "message": f"DPSR is {dsr['dsr_strict']}% — reach 80% to unlock the next Champion tier."})

    return {"telesales_rep": telesales_rep, "recommendations": recs,
            "read_only": True}
