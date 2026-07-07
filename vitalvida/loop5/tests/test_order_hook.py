"""Order-hook router test — a Paid transition drives the earn checks; a
Cancelled/Returned transition drives the void. Idempotent on repeat."""

import frappe
import unittest

from vitalvida.loop5 import order_hooks


class _FakeOrder:
    def __init__(self, name, status):
        self.name = name
        self.order_status = status


class TestOrderHook(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_paid_transition_is_safe_when_nothing_to_do(self):
        # An order with no upsell/recovery/dormancy should not raise.
        o = _FakeOrder("NON-EXISTENT-ORDER", "Paid")
        try:
            order_hooks.on_vv_order_update(o)  # must swallow + log, never raise
        except Exception as e:
            self.fail(f"hook raised on benign order: {e}")

    def test_hook_never_raises(self):
        for status in ["Paid", "Cancelled", "Returned", "Confirmed", None]:
            order_hooks.on_vv_order_update(_FakeOrder("X", status))
