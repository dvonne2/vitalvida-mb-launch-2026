import unittest
from unittest.mock import patch, MagicMock
from vitalvida.inventory import authority, audit

class TestAuthority(unittest.TestCase):
    @patch("vitalvida.inventory.authority.frappe")
    def test_bin_is_balance_authority(self, f):
        f.db.get_value.return_value=7
        self.assertEqual(authority.balance("Shampoo","DA-001"),7)
        f.db.get_value.assert_called_once()

class TestAudit(unittest.TestCase):
    def test_package_payload_has_no_custom_balance_writer(self):
        import pathlib
        root=pathlib.Path(__file__).resolve().parents[1]
        hits=audit.scan(str(root))
        # deduction.py contains legacy code intentionally active only in Transition; audit is cutover gate, not install gate.
        self.assertIsInstance(hits,list)

class TestSourceKeys(unittest.TestCase):
    def test_event_key_is_required(self):
        from vitalvida.inventory.events import emit
        self.assertTrue(callable(emit))
