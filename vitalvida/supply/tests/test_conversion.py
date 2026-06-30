"""
Loop 3 — conversion guard tests. These DO touch the DB, so run only in a safe
context. They assert the Loop 2 custody gate is honored: an ineligible DA's
recommendation must NOT convert to a consignment, and a denied action is logged.

VERIFY BEFORE APPLYING: uses a throwaway recommendation + a DA known to be
ineligible. On production, prefer a ZZ-TEST DA created via raw insert (see Loop 2
test harness) rather than a real DA.
"""
import unittest
import frappe
from vitalvida.supply import conversion as C


class TestConversionRespectsCustody(unittest.TestCase):
    def test_blocked_da_does_not_convert(self):
        # Find any DA currently ineligible per can_hold_custody
        from vitalvida.consignment import can_hold_custody
        das = frappe.get_all("Delivery Agent", filters={"active": 1}, fields=["name"])
        blocked = None
        for d in das:
            if not can_hold_custody(d["name"]).get("allowed"):
                blocked = d["name"]; break
        if not blocked:
            self.skipTest("No ineligible DA available to test the custody gate.")

        rec = frappe.get_doc({
            "doctype": "Supply Recommendation", "recommendation_date": frappe.utils.nowdate(),
            "delivery_agent": blocked, "product": "Shampoo", "recommended_quantity": 5,
            "recommendation_type": "Send Stock to DA", "status": "Approved",
            "idempotency_key": f"TEST|{blocked}|Shampoo|{frappe.utils.nowdate()}",
        }).insert(ignore_permissions=True)
        frappe.db.commit()

        before = frappe.db.count("Denied Action Log", {"action_type": "Custody"})
        with self.assertRaises(Exception):
            C.convert_to_consignment(rec.name)
        after = frappe.db.count("Denied Action Log", {"action_type": "Custody"})

        self.assertGreater(after, before, "A denied custody action should be logged.")
        rec.reload()
        self.assertFalse(rec.converted_to_consignment, "No consignment should be created.")

        # cleanup
        frappe.delete_doc("Supply Recommendation", rec.name, force=True, ignore_permissions=True)
        frappe.db.commit()


if __name__ == "__main__":
    unittest.main()
