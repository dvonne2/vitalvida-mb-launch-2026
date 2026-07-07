import unittest
from vitalvida.customer_relationship.identity import normalize_phone


class TestPhoneNormalization(unittest.TestCase):
    def test_canonical_forms(self):
        cases = {
            "08031234567": "2348031234567",
            "0803 123 4567": "2348031234567",
            "+2348031234567": "2348031234567",
            "2348031234567": "2348031234567",
            "8031234567": "2348031234567",
        }
        for raw, exp in cases.items():
            self.assertEqual(normalize_phone(raw), exp, f"{raw} should canonicalize to {exp}")

    def test_unresolvable_returns_none(self):
        for bad in ["", None, "garbage", "234803123456", "123"]:
            self.assertIsNone(normalize_phone(bad), f"{bad!r} should be unresolvable")
