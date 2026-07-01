"""
Customer Revival Champion.

A customer counts as revived when they had NO Delivered & Paid order for
>= revival_dormancy_days (default 30) and then complete a NEW Delivered & Paid
order through a rep. A phone call alone earns nothing.

Source of truth for "Delivered & Paid" is VV Order.paid_at (the raw immutable
event). This module READS order history only; it never writes Loop 4 truth.
If you prefer to read Loop 4 `Customer Outcome` instead, swap `_last_dp_before`
— it is deliberately the only coupling point.
"""

import frappe
from frappe.utils import add_days, getdate

from vitalvida.loop5 import settings as l5s
from vitalvida.loop5 import events as l5e
from vitalvida.loop5 import champions as l5c


def _last_dp_before(customer_phone: str, before_dt) -> str | None:
    """Most recent Delivered & Paid date for this customer strictly before the
    given datetime. Customer identity = customer_phone (as used by cart
    recovery). Read-only over VV Order."""
    row = frappe.db.sql(
        """
        SELECT MAX(paid_at) AS last_dp
        FROM `tabVV Order`
        WHERE customer_phone = %s
          AND order_status = 'Paid'
          AND paid_at IS NOT NULL
          AND paid_at < %s
        """,
        (customer_phone, before_dt), as_dict=True,
    )
    return row[0].last_dp if row and row[0].last_dp else None


def evaluate_revival_for_order(order: str, dry_run: bool = False) -> dict:
    """Called when an order reaches Delivered & Paid. Emits a revival Business
    Event + Customer Revival State if the customer was dormant >= window."""
    o = frappe.db.get_value(
        "VV Order", order,
        ["customer_phone", "telesales_rep", "paid_at", "order_status"],
        as_dict=True,
    )
    if not o or o.order_status != "Paid" or not o.paid_at:
        return {"revived": False, "reason": "not_delivered_and_paid"}

    prev_dp = _last_dp_before(o.customer_phone, o.paid_at)
    window = l5s.revival_dormancy_days()

    if prev_dp is None:
        return {"revived": False, "reason": "no_prior_dp_first_time_customer"}

    dormant_days = (getdate(o.paid_at) - getdate(prev_dp)).days
    if dormant_days < window:
        return {"revived": False, "reason": "not_dormant_enough",
                "dormant_days": dormant_days}

    source_event = f"revival::{o.customer_phone}::{o.paid_at}"
    if frappe.db.exists("Customer Revival State", {"revived_order": order}):
        return {"revived": False, "reason": "already_recorded"}

    if dry_run:
        return {"revived": True, "dormant_days": dormant_days, "dry_run": True}

    frappe.get_doc({
        "doctype": "Customer Revival State",
        "customer": o.customer_phone,
        "last_dp_date": prev_dp,
        "dormant_days": dormant_days,
        "revived_order": order,
        "telesales_rep": o.telesales_rep,
        "revived_flag": 1,
    }).insert(ignore_permissions=True)

    l5e.emit_business_event(
        l5e.REVIVAL, order=order, customer=o.customer_phone,
        telesales_rep=o.telesales_rep, source_ref=source_event,
    )
    frappe.db.commit()

    # Ladder bonus is evaluated on cumulative reactivations for the rep/period.
    _maybe_emit_ladder_bonus(o.telesales_rep)
    return {"revived": True, "dormant_days": dormant_days}


def _maybe_emit_ladder_bonus(telesales_rep: str) -> None:
    """Emit the revival ladder bonus delta when a rep crosses a tier."""
    count = frappe.db.count("Customer Revival State",
                            {"telesales_rep": telesales_rep, "revived_flag": 1})
    total = l5s.revival_bonus_for(count)
    if total <= 0:
        return
    source_event = f"revival_ladder::{telesales_rep}::{count}"
    if l5c.already_emitted(l5c.CHAMPION_REVIVAL, source_event):
        return
    # Emit the *incremental* amount over what was already earned at lower tiers.
    prior = _prior_ladder_total(telesales_rep, count)
    delta = total - prior
    if delta <= 0:
        return
    l5c.emit_bonus_event(
        telesales_rep=telesales_rep, champion_type=l5c.CHAMPION_REVIVAL,
        amount=delta, source_event=source_event,
        justification=f"Customer Revival ladder — {count} reactivated",
    )


def _prior_ladder_total(telesales_rep: str, count: int) -> float:
    """Sum of ladder totals already emitted for this rep below current count."""
    prev_counts = frappe.get_all(
        "Bonus Approval Request",
        filters={"champion_type": l5c.CHAMPION_REVIVAL,
                 "source_event": ["like", f"revival_ladder::{telesales_rep}::%"]},
        fields=["source_event"],
    )
    highest_prior = 0
    for r in prev_counts:
        try:
            c = int(r.source_event.rsplit("::", 1)[1])
            if c < count:
                highest_prior = max(highest_prior, c)
        except Exception:
            continue
    return l5s.revival_bonus_for(highest_prior) if highest_prior else 0.0
