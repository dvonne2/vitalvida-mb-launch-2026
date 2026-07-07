"""
VV Order event router.

Wired via hooks.py doc_events["VV Order"]["on_update"] (Loop 5 APPENDS its
handler; it does not replace the existing reconciliation/email handlers).

On every VV Order update we detect the transition into a terminal state and
drive the Loop 5 spine. All handlers are idempotent (guarded by "already
emitted" checks) so repeated on_update calls never double-pay.
"""

import frappe

from vitalvida.loop5 import upsell as l5_upsell
from vitalvida.loop5 import revival as l5_revival
from vitalvida.loop5 import abandoned_cart as l5_cart


def on_vv_order_update(doc, method=None):
    """Entry point registered in hooks.py. `doc` is the VV Order."""
    try:
        status = getattr(doc, "order_status", None)

        if status == "Paid":
            # Delivered & Paid is the gate. Fire all earn-checks (each is a
            # no-op unless its own precondition holds and not already emitted).
            l5_upsell.maybe_earn_upsell_commission(doc.name)
            l5_revival.evaluate_revival_for_order(doc.name)
            l5_cart.evaluate_cart_recovery_for_order(doc.name)

        elif status in ("Cancelled", "Returned"):
            # RTO / cancellation voids upsell commission via reversal event.
            l5_upsell.void_upsell_commission(doc.name)

    except Exception as e:
        # Never let Loop 5 break the core order save. Log and move on.
        frappe.log_error(
            f"Loop5: on_vv_order_update failed for {getattr(doc, 'name', '?')}: {e}",
            "Loop5 Order Hook Error",
        )
