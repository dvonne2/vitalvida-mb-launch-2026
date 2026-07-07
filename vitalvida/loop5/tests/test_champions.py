"""
Champion emission + payroll interaction tests (v5.1). Run in bench:
  bench --site vitalvida.systemforce.ng run-tests --module vitalvida.loop5.tests.test_champions
"""

import frappe
import unittest

from vitalvida.loop5 import champions as l5c
from vitalvida.loop5 import payroll_seam as seam


class TestChampions(unittest.TestCase):
    def setUp(self):
        self.rep = _ensure_closer("TEST-CLOSER-CH")
        self.emp = _ensure_employee("TEST-EMP-CH", linked_closer=self.rep)

    def tearDown(self):
        frappe.db.rollback()

    def test_sub_threshold_bonus_is_persisted_and_payable(self):
        """REGRESSION: a 1,000 upsell bonus is below the FC threshold. It must
        still create an Approved, payable Bonus Approval Request — not vanish."""
        res = l5c.emit_bonus_event(self.rep, l5c.CHAMPION_UPSELL, 1000,
                                   source_event="ups::regress::1")
        self.assertTrue(res["emitted"])
        req = res["request"]
        self.assertEqual(
            frappe.db.get_value("Bonus Approval Request", req, "status"),
            "Approved")
        # payroll must see and pay it
        total = seam.compute_champion_bonuses(self.emp, "2026-07-01",
                                              "2026-08-01", mark_paid=False)
        self.assertEqual(total, 1000)

    def test_above_threshold_bonus_is_pending_not_paid(self):
        res = l5c.emit_bonus_event(self.rep, l5c.CHAMPION_DPSR, 50000,
                                   source_event="dpsr::big::1")
        self.assertTrue(res["emitted"])
        self.assertEqual(
            frappe.db.get_value("Bonus Approval Request", res["request"], "status"),
            "Pending")
        # not payable until a human approves
        total = seam.compute_champion_bonuses(self.emp, "2026-07-01",
                                              "2026-08-01", mark_paid=False)
        self.assertEqual(total, 0)

    def test_source_event_dedupe(self):
        a = l5c.emit_bonus_event(self.rep, l5c.CHAMPION_UPSELL, 1000, "dup::1")
        b = l5c.emit_bonus_event(self.rep, l5c.CHAMPION_UPSELL, 1000, "dup::1")
        self.assertTrue(a["emitted"])
        self.assertFalse(b["emitted"])
        self.assertEqual(b["reason"], "already_emitted")

    def test_bonus_paid_only_once(self):
        """Two payroll passes must not double-pay the same event."""
        l5c.emit_bonus_event(self.rep, l5c.CHAMPION_UPSELL, 1000, "once::1")
        first = seam.compute_champion_bonuses(self.emp, "2026-07-01",
                                              "2026-08-01", mark_paid=True)
        second = seam.compute_champion_bonuses(self.emp, "2026-07-01",
                                               "2026-08-01", mark_paid=True)
        self.assertEqual(first, 1000)
        self.assertEqual(second, 0)


def _ensure_closer(name):
    if not frappe.db.exists("Telesales Closer", name):
        frappe.get_doc({"doctype": "Telesales Closer", "closer_name": name,
                        "is_active": 1}).insert(ignore_permissions=True)
    return name

def _ensure_employee(name, linked_closer=None):
    if not frappe.db.exists("VV Employee", name):
        frappe.get_doc({"doctype": "VV Employee", "employee_name": name,
                        "is_active": 1, "base_salary": 120000,
                        "commission_eligible": 1,
                        "linked_closer": linked_closer}).insert(ignore_permissions=True)
    return name
