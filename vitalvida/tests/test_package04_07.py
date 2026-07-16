from pathlib import Path
import ast
from frappe.tests.utils import FrappeTestCase

ROOT = Path(__file__).resolve().parents[1]

class TestPackage0407Architecture(FrappeTestCase):
    def test_no_parallel_custody_doctype_shipped(self):
        self.assertFalse((ROOT / "vitalvida/doctype/custody_transfer_event").exists())

    def test_one_inventory_cutover_switch(self):
        me = Path(__file__).resolve()
        payload = "\n".join(
            p.read_text() for p in ROOT.rglob("*.py")
            if p.resolve() != me
        )
        self.assertNotIn("custody" + "_cutover", payload)
        self.assertIn("vitalvida.inventory.authority", payload)

    def test_manual_confirmation_requires_authority(self):
        text = (ROOT / "api/operations.py").read_text()
        self.assertIn("Payment Reconciliation Log", text)
        self.assertIn("_finalize_order(order_id, recon_name)", text)
        self.assertIn("_finalize_order(order_id, existing_recon)", text)

    def test_e1_has_two_consumers_and_repair(self):
        text = (ROOT / "domain/payments.py").read_text()
        self.assertIn("domain.fulfilment.on_payment_confirmed", text)
        self.assertIn("domain.finance_contract.on_payment_confirmed", text)
        self.assertIn("def repair_missing_e1", text)

    def test_delivery_note_delegates_to_package03(self):
        text = (ROOT / "domain/fulfilment.py").read_text()
        self.assertIn("from vitalvida.inventory.movements import delivery_note_for_order", text)


    def test_e26_bucket_a_uses_standard_erpnext_authority(self):
        text = (ROOT / "patches/v7_0/seed_operational_events.py").read_text()
        self.assertIn('"E26_TRANSPORT_COST_INCURRED", "Transport Cost Incurred", "A"', text)
        self.assertIn('"Journal Entry", "", "domain/finance_contract.py (Package 08 hand-off)"', text)
        self.assertNotIn('"Stock Dispatch", "", "domain/logistics.py"', text)

    def test_python_payload_parses(self):
        for path in ROOT.rglob("*.py"):
            ast.parse(path.read_text(), filename=str(path))
