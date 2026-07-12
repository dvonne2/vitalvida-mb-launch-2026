import frappe
from frappe.tests.utils import FrappeTestCase
from vitalvida.integration import registry


class TestRegistry(FrappeTestCase):
    """Assumes the seed patch has populated the register."""

    def test_seed_present(self):
        self.assertTrue(frappe.db.exists("Event Definition",
                        {"event_key": "E8_SETTLEMENT_PAID"}))

    def test_bucket_a_has_no_custom_owner(self):
        d = registry.get_definition("E8_SETTLEMENT_PAID")
        self.assertEqual(d["bucket"], "A")
        self.assertEqual(d["authoritative_doctype"], "Payment Entry")

    def test_authorized_emitter_guard(self):
        # DA Payout Record must not emit the DA-fee-earned event that the
        # earning record owns; the guard should refuse a wrong source.
        with self.assertRaises(frappe.ValidationError):
            registry.assert_authorized_emitter("E5_DA_FEE_EARNED", "Some Random Doctype")
