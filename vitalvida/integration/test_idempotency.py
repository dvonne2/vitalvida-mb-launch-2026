import frappe
from frappe.tests.utils import FrappeTestCase
from vitalvida.integration.idempotency import ensure_once, source_key


class TestIdempotency(FrappeTestCase):
    def _filters(self):
        return {"event_key": "TEST_E", "source_name": "TEST-1",
                "consumer_method": "x"}

    def tearDown(self):
        for n in frappe.get_all("Integration Outbox",
                                filters={"source_name": "TEST-1"}, pluck="name"):
            frappe.delete_doc("Integration Outbox", n, force=True)

    def test_creates_once(self):
        vals = {**self._filters(), "source_doctype": "VV Order", "status": "Pending"}
        r1 = ensure_once("Integration Outbox", self._filters(), vals)
        self.assertTrue(r1["created"])
        r2 = ensure_once("Integration Outbox", self._filters(), vals)
        self.assertFalse(r2["created"])
        self.assertTrue(r2["already_emitted"])
        self.assertEqual(r1["name"], r2["name"])

    def test_race_recovery(self):
        # Simulate the losing racer: row already exists when we attempt insert.
        vals = {**self._filters(), "source_doctype": "VV Order", "status": "Pending"}
        first = ensure_once("Integration Outbox", self._filters(), vals)
        frappe.db.commit()
        second = ensure_once("Integration Outbox", self._filters(), vals)
        self.assertEqual(first["name"], second["name"])
        self.assertFalse(second["created"])

    def test_source_key_stable(self):
        self.assertEqual(source_key("a", 1, None), source_key("a", 1, None))
        self.assertNotEqual(source_key("a", 1), source_key("a", 2))
