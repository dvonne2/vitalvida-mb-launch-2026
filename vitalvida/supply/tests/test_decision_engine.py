"""
Loop 3 — decision engine unit tests. Run headless:
  cd sites && sudo -u frappe ../env/bin/python -m pytest \
    ../apps/vitalvida/vitalvida/supply/tests/test_decision_engine.py
(or via `bench run-tests --module vitalvida.supply.tests.test_decision_engine`)

These tests exercise PURE calculations with injected stock/bundle dicts — they do
NOT write to the database, so they are safe to run on production read-only.

NOTE (recon 2026-06-29): production currently has 0 Paid orders, so live sales
velocity is 0 for every DA (the engine correctly falls back to MSS-only). Velocity
behaviour must therefore be tested with SYNTHETIC data (injected as below), never
by assuming existing Paid-order rows.
"""
import unittest
from vitalvida.supply import decision_engine as E


class TestCalculations(unittest.TestCase):
    def setUp(self):
        self.bundles = [{"name": "SELF LOVE PLUS", "price": 66750.0,
                         "reqs": {"Shampoo": 1, "Conditioner": 1, "Pomade": 1}}]

    def test_sellable_bundles_and_bottleneck(self):
        stock = {"Shampoo": 4, "Conditioner": 20, "Pomade": 18}
        sellable, bottleneck = E.sellable_bundles(stock, self.bundles[0])
        self.assertEqual(sellable, 4)
        self.assertEqual(bottleneck, "Shampoo")

    def test_bundle_broken_when_zero(self):
        stock = {"Shampoo": 0, "Conditioner": 30, "Pomade": 30}
        sellable, bottleneck = E.sellable_bundles(stock, self.bundles[0])
        self.assertEqual(sellable, 0)
        self.assertEqual(bottleneck, "Shampoo")

    def test_lofr_risk_levels(self):
        self.assertEqual(E.lofr_risk_level(0, 3, 0, True), "Red")
        self.assertEqual(E.lofr_risk_level(2, 3, 5, False), "Red")     # below MSS
        self.assertEqual(E.lofr_risk_level(10, 3, 5, False), "Amber")  # <7d cover
        self.assertEqual(E.lofr_risk_level(50, 3, 20, False), "Green")

    def test_priority_ineligible_da_penalised(self):
        plan = {"lofr_risk": "Red", "bundle_bottleneck": "Shampoo",
                "revenue_unlocked": 100000, "average_daily_sales": 5}
        eligible = E.priority_score(plan, da_eligible=True)
        blocked = E.priority_score(plan, da_eligible=False)
        self.assertGreater(eligible, blocked)
        self.assertEqual(eligible - blocked, 80)  # the eligibility penalty

    def test_classify_emergency_on_zero_stock(self):
        plan = {"current_stock": 0, "bundle_bottleneck": "", "days_of_cover": 0,
                "minimum_service_stock": 3, "average_daily_sales": 2,
                "recommended_quantity": 10}
        self.assertEqual(E.classify_recommendation(plan, da_eligible=True),
                         "Emergency Replenishment")

    def test_classify_do_not_replenish_when_covered(self):
        plan = {"current_stock": 100, "bundle_bottleneck": "", "days_of_cover": 28,
                "minimum_service_stock": 3, "average_daily_sales": 2,
                "recommended_quantity": 0}
        self.assertEqual(E.classify_recommendation(plan, da_eligible=True),
                         "Do Not Replenish")

    def test_ineligible_da_gets_replace_add(self):
        plan = {"current_stock": 0, "bundle_bottleneck": "Shampoo", "days_of_cover": 0,
                "minimum_service_stock": 3, "average_daily_sales": 2, "recommended_quantity": 5}
        self.assertEqual(E.classify_recommendation(plan, da_eligible=False),
                         "Replace / Add DA")


if __name__ == "__main__":
    unittest.main()
