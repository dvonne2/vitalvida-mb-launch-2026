"""DPSR delta logic: rising tiers pay only the difference."""
import frappe
import unittest

from vitalvida.loop5 import settings as l5s
from vitalvida.loop5 import dpsr_champion as dc


class TestDpsrDelta(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_ladder_tier_values(self):
        self.assertEqual(l5s.dpsr_bonus_for(59), 0)
        self.assertEqual(l5s.dpsr_bonus_for(80), 50000)
        self.assertEqual(l5s.dpsr_bonus_for(90), 70000)
        self.assertEqual(l5s.dpsr_bonus_for(100), 100000)

    def test_prior_emitted_sums_only_non_voided(self):
        rep, period = "TEST-CLOSER-DPSR", "2026-06-29"
        _mk_dpsr(rep, period, 80, 50000, voided=0)
        _mk_dpsr(rep, period, 70, 15000, voided=1)   # voided must not count
        prior = dc._dpsr_already_emitted(rep, period)
        self.assertEqual(prior, 50000)   # only the non-voided 80% tier

    def test_delta_is_difference_not_stack(self):
        rep, period = "TEST-CLOSER-DPSR2", "2026-06-29"
        _mk_dpsr(rep, period, 80, 50000, voided=0)   # already earned 50k at 80%
        prior = dc._dpsr_already_emitted(rep, period)
        target_at_90 = l5s.dpsr_bonus_for(90)          # 70000
        delta = target_at_90 - prior
        self.assertEqual(delta, 20000)   # pay 20k, NOT 70k on top of 50k


def _mk_dpsr(rep, period, dsr, amount, voided):
    frappe.get_doc({"doctype": "Bonus Approval Request",
                    "employee": "Administrator",  # placeholder; test reads amounts only
                    "employee_type": "Telesales", "bonus_amount": amount,
                    "status": "Approved", "champion_type": "DPSR",
                    "source_event": f"dpsr::{rep}::{period}::{dsr}",
                    "l5_voided": voided}).insert(ignore_permissions=True)
