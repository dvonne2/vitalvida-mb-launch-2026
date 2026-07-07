import frappe
import unittest

from vitalvida.loop5 import payroll_seam as seam


class TestPayrollSeam(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_only_approved_champion_bonuses_are_paid(self):
        emp = _ensure_employee("TEST-EMP-L5")
        # a Pending request must NOT be paid
        _mk_request(emp, 1000, status="Pending", champion="Upsell", src="s1")
        # an Approved request MUST be paid
        _mk_request(emp, 5000, status="Approved", champion="DPSR", src="s2")
        total = seam.compute_champion_bonuses(emp, "2026-07-01", "2026-08-01",
                                              mark_paid=False)
        self.assertEqual(total, 5000)


def _ensure_employee(name):
    if not frappe.db.exists("VV Employee", name):
        frappe.get_doc({"doctype": "VV Employee", "employee_name": name,
                        "is_active": 1, "base_salary": 120000}).insert(ignore_permissions=True)
    return name

def _mk_request(emp, amt, status, champion, src):
    frappe.get_doc({"doctype": "Bonus Approval Request", "employee": emp,
                    "employee_type": "Telesales", "bonus_amount": amt,
                    "status": status, "champion_type": champion,
                    "source_event": src}).insert(ignore_permissions=True)


class TestSeamSafety(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_preview_does_not_mark_paid(self):
        from vitalvida.loop5 import payroll_seam as seam
        emp = _ensure_employee("TEST-EMP-SAFE")
        _mk_request(emp, 1000, status="Approved", champion="Upsell", src="safe1")
        # default mark_paid=False must not mutate l5_paid
        total = seam.compute_champion_bonuses(emp, "2026-07-01", "2026-08-01")
        self.assertEqual(total, 1000)
        req = frappe.db.get_value("Bonus Approval Request",
                                  {"source_event": "safe1"}, "l5_paid")
        self.assertIn(req, (0, None))   # NOT paid by a preview/dry-run

    def test_settle_marks_paid_once(self):
        from vitalvida.loop5 import payroll_seam as seam
        emp = _ensure_employee("TEST-EMP-SETTLE")
        _mk_request(emp, 5000, status="Approved", champion="DPSR", src="settle1")
        first = seam.settle_champion_bonuses(emp)
        second = seam.settle_champion_bonuses(emp)
        self.assertEqual(first, 5000)
        self.assertEqual(second, 0)   # already settled, never twice
