import frappe
import unittest


class TestImmutability(unittest.TestCase):
    def tearDown(self):
        frappe.db.rollback()

    def test_revenue_event_cannot_be_deleted(self):
        ev = frappe.get_doc({"doctype": "Revenue Business Event",
                             "event_type": "Upsell", "value": 100}).insert(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            ev.delete()

    def test_commercial_change_log_immutable(self):
        c = frappe.get_doc({"doctype": "Commercial Change Log",
                            "change_type": "Upsell", "field_before": "a",
                            "field_after": "b"}).insert(ignore_permissions=True)
        c.field_after = "TAMPERED"
        with self.assertRaises(frappe.PermissionError):
            c.save()
