"""
Abandoned Cart Recovery Champion.

cart_recovery.py is a NOTIFICATION sequencer (M7). It earns nothing. This module
READS `Cart Recovery State` to know an order was in recovery, then credits the
rep ONLY when that recovered order reaches Delivered & Paid. Recovering the cart
alone earns nothing.
"""

import frappe

from vitalvida.loop5 import settings as l5s
from vitalvida.loop5 import events as l5e
from vitalvida.loop5 import champions as l5c


def _order_was_in_recovery(order: str) -> bool:
    return bool(frappe.db.exists("Cart Recovery State", {"order": order}))


def evaluate_cart_recovery_for_order(order: str, dry_run: bool = False) -> dict:
    """Called when an order reaches Delivered & Paid. If the order had a Cart
    Recovery State (i.e. it was an abandoned/partial cart that got recovered),
    credit the rep once."""
    if not l5e.order_is_delivered_and_paid(order):
        return {"credited": False, "reason": "not_delivered_and_paid"}
    if not _order_was_in_recovery(order):
        return {"credited": False, "reason": "not_a_recovered_cart"}

    o = frappe.db.get_value("VV Order", order,
                            ["telesales_rep", "customer_phone"], as_dict=True)
    source_event = f"cart::{order}"
    if l5c.already_emitted(l5c.CHAMPION_CART, source_event):
        return {"credited": False, "reason": "already_credited"}

    if dry_run:
        return {"credited": True, "dry_run": True}

    l5e.emit_business_event(
        l5e.CART_RECOVERED, order=order, customer=o.customer_phone,
        telesales_rep=o.telesales_rep, source_ref=source_event,
    )

    # Ladder on cumulative recovered-and-paid orders for the rep.
    count = frappe.db.count(
        "Revenue Business Event",
        {"event_type": l5e.CART_RECOVERED, "telesales_rep": o.telesales_rep},
    )
    total = l5s.cart_bonus_for(count)
    prior = _prior_cart_total(o.telesales_rep, count)
    delta = total - prior
    if delta > 0:
        l5c.emit_bonus_event(
            telesales_rep=o.telesales_rep, champion_type=l5c.CHAMPION_CART,
            amount=delta, source_event=f"cart_ladder::{o.telesales_rep}::{count}",
            justification=f"Abandoned Cart ladder — {count} recovered & paid",
        )
    frappe.db.commit()
    return {"credited": True, "recovered_count": count, "ladder_delta": delta}


def _prior_cart_total(telesales_rep: str, count: int) -> float:
    prev = frappe.get_all(
        "Bonus Approval Request",
        filters={"champion_type": l5c.CHAMPION_CART,
                 "source_event": ["like", f"cart_ladder::{telesales_rep}::%"]},
        fields=["source_event"],
    )
    highest_prior = 0
    for r in prev:
        try:
            c = int(r.source_event.rsplit("::", 1)[1])
            if c < count:
                highest_prior = max(highest_prior, c)
        except Exception:
            continue
    return l5s.cart_bonus_for(highest_prior) if highest_prior else 0.0
