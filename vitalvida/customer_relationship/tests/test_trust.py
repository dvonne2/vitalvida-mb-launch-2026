import unittest
from vitalvida.customer_relationship.trust import SIGNAL_DELTA, _band, PROVISIONAL_START


class TestTrustModel(unittest.TestCase):
    def test_positive_and_negative_signals(self):
        self.assertGreater(SIGNAL_DELTA["Outcome Achieved"], 0)
        self.assertGreater(SIGNAL_DELTA["Delivered"], 0)
        self.assertLess(SIGNAL_DELTA["Delivery Failed"], 0)
        self.assertLess(SIGNAL_DELTA["Complaint Filed"], 0)

    def test_bands(self):
        self.assertEqual(_band(90), "Very High")
        self.assertEqual(_band(75), "High")
        self.assertEqual(_band(50), "Medium")
        self.assertEqual(_band(10), "Low")
        self.assertEqual(_band(50, provisional=True), "Provisional")

    def test_provisional_start_neutral(self):
        self.assertEqual(PROVISIONAL_START, 50)
