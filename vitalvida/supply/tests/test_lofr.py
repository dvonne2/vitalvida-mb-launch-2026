"""Loop 3 — LOFR calculation sanity tests (read-only)."""
import unittest
from vitalvida.supply import lofr as L


class TestLOFR(unittest.TestCase):
    def test_empty_window_is_100(self):
        # A DA with no assigned orders in a future window -> LOFR defaults to 100 (no failures)
        m = L.calculate_lofr("2099-01-01", "2099-01-02", da="__nonexistent__")
        self.assertEqual(m["total_orders"], 0)
        self.assertEqual(m["lofr_percent"], 100.0)


if __name__ == "__main__":
    unittest.main()
