"""
Loop 5 upsell tests. Run in the bench:
    bench --site vitalvida.systemforce.ng run-tests --module vitalvida.loop5.tests.test_upsell

These are written against the REAL confirmed interfaces. They create their own
VV Order fixtures and clean up. They do NOT touch payroll.
"""

import frappe
import unittest

from vitalvida.loop5 import upsell as l5_upsell
from vitalvida.loop5 import settings as l5s


class TestUpsell(unittest.TestCase):
    def setUp(self):
        self.rep = _ensure_closer("TEST-CLOSER-L5")
        self.order = _make_order(self.rep, value=32750)

    def tearDown(self):
        frappe.db.rollback()

    def test_upsell_keeps_same_order_id(self):
        res = l5_upsell.record_upsell(self.order, "SELF LOVE PLUS B2GOF", 66750,
                                      reason="test")
        self.assertEqual(res["order"], self.order)  # same id, order evolves
        self.assertEqual(res["commission_status"], "Pending")
        self.assertTrue(frappe.db.exists("Upsell Event", res["upsell_event"]))
        self.assertTrue(frappe.db.exists("Commercial Change Log", {"order": self.order}))

    def test_upsell_must_increase_value(self):
        with self.assertRaises(frappe.ValidationError):
            l5_upsell.record_upsell(self.order, "cheaper", 100)

    def test_no_commission_until_delivered_and_paid(self):
        l5_upsell.record_upsell(self.order, "B2GOF", 66750)
        # order still not Paid -> no earn
        res = l5_upsell.maybe_earn_upsell_commission(self.order)
        self.assertFalse(res["earned"])

    def test_min_incremental_blocks_tiny_upsell(self):
        # Configure a high floor so a small bump does not qualify
        _set_setting("upsell_min_incremental_value", 20000)
        l5_upsell.record_upsell(self.order, "barely", 35000)  # +2250 only
        _mark_paid(self.order)
        res = l5_upsell.maybe_earn_upsell_commission(self.order)
        self.assertFalse(res["earned"])
        self.assertEqual(res["reason"], "below_min_incremental")

    def test_rto_voids_commission_via_reversal(self):
        r = l5_upsell.record_upsell(self.order, "B2GOF", 66750)
        _set_status(self.order, "Returned")
        out = l5_upsell.void_upsell_commission(self.order)
        self.assertTrue(out["voided"])
        self.assertEqual(
            frappe.db.get_value("Upsell Event", r["upsell_event"], "commission_status"),
            "Voided")


# ---- helpers ----
def _ensure_closer(name):
    if not frappe.db.exists("Telesales Closer", name):
        frappe.get_doc({"doctype": "Telesales Closer", "closer_name": name,
                        "is_active": 1}).insert(ignore_permissions=True)
    return name

def _make_order(rep, value):
    o = frappe.get_doc({"doctype": "VV Order", "telesales_rep": rep,
                        "order_status": "Confirmed", "package_name": "SELF LOVE PLUS",
                        "product_amount": value, "total_payable": value,
                        "customer_phone": "080TEST0001"}).insert(ignore_permissions=True)
    return o.name

def _mark_paid(order):
    _set_status(order, "Paid")

def _set_status(order, status):
    frappe.db.set_value("VV Order", order, "order_status", status)
    if status == "Paid":
        frappe.db.set_value("VV Order", order, "paid_at", frappe.utils.now_datetime())

def _set_setting(key, val):
    if frappe.db.exists("DocType", "VV Commission Settings"):
        frappe.db.set_value("VV Commission Settings", None, key, val)


class TestUpsellVoidExcludedFromPay(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_voided_upsell_bonus_is_not_paid(self):
        from vitalvida.loop5 import champions as l5c
        from vitalvida.loop5 import payroll_seam as seam
        from vitalvida.loop5 import upsell as up
        rep = _ensure_closer("TEST-CLOSER-VOID")
        if not frappe.db.exists("VV Employee", "TEST-EMP-VOID"):
            frappe.get_doc({"doctype": "VV Employee", "employee_name": "TEST-EMP-VOID",
                            "is_active": 1, "base_salary": 100000,
                            "commission_eligible": 1,
                            "linked_closer": rep}).insert(ignore_permissions=True)
        order = _make_order(rep, 32750)
        r = up.record_upsell(order, "B2GOF", 66750)
        _mark_paid(order)
        up.maybe_earn_upsell_commission(order)     # emits 1000 (auto-approved)
        # now the order RTOs
        _set_status(order, "Returned")
        up.void_upsell_commission(order)
        total = seam.compute_champion_bonuses("TEST-EMP-VOID", "2026-07-01",
                                              "2026-08-01", mark_paid=False)
        self.assertEqual(total, 0)   # voided money is never paid


class TestUpsellStatusGate(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_cannot_upsell_paid_or_cancelled(self):
        from vitalvida.loop5 import upsell as up
        rep = _ensure_closer("TEST-CLOSER-GATE")
        for bad in ("Paid", "Cancelled", "Returned"):
            o = _make_order(rep, 32750)
            _set_status(o, bad)
            with self.assertRaises(frappe.ValidationError):
                up.record_upsell(o, "B2GOF", 66750)
