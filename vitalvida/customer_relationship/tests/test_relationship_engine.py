"""
Loop 4 — DB-backed relationship engine tests.
These require a Frappe test DB (allow_tests=true). They are NOT run on production
(which has allow_tests=false by design); they run in a disposable test site/CI.
Each test cleans up after itself.
"""
import frappe
import unittest
from vitalvida.customer_relationship.identity import normalize_phone, resolve_customer
from vitalvida.customer_relationship.trust import apply_trust_signal, recompute_trust
from vitalvida.customer_relationship.health import compute_health
from vitalvida.customer_relationship.nba import compute_nba

TEST_PHONE_RAW = "08039998877"
TEST_KEY = "2348039998877"


def _cleanup():
    for dt in ["Customer Trust Log", "Customer Timeline Event", "Relationship NBA Log",
               "Customer Complaint", "Customer Journey State"]:
        for r in frappe.get_all(dt, filters={"customer": TEST_KEY}, fields=["name"]):
            frappe.delete_doc(dt, r["name"], force=True, ignore_permissions=True)
    if frappe.db.exists("Customer Profile", TEST_KEY):
        frappe.delete_doc("Customer Profile", TEST_KEY, force=True, ignore_permissions=True)


class TestRelationshipEngine(unittest.TestCase):
    def setUp(self):
        _cleanup()
        # ensure settings single exists with defaults
        if not frappe.db.exists("Loop 4 Settings", "Loop 4 Settings"):
            frappe.get_doc({"doctype": "Loop 4 Settings"}).insert(ignore_permissions=True)

    def tearDown(self):
        _cleanup()
        frappe.db.rollback()

    def test_resolve_creates_one_profile_per_canonical_phone(self):
        k1 = resolve_customer(TEST_PHONE_RAW, name="Test User")
        k2 = resolve_customer("+2348039998877")  # different format, same person
        self.assertEqual(k1, TEST_KEY)
        self.assertEqual(k2, TEST_KEY)  # must NOT create a second profile
        self.assertEqual(frappe.db.count("Customer Profile", {"phone": TEST_KEY}), 1)

    def test_unresolvable_phone_creates_nothing(self):
        self.assertIsNone(resolve_customer("garbage"))
        self.assertIsNone(resolve_customer(""))

    def test_trust_signal_writes_audit_and_updates_score(self):
        resolve_customer(TEST_PHONE_RAW, name="Test User")
        before = frappe.db.get_value("Customer Profile", TEST_KEY, "trust_score") or 50
        new = apply_trust_signal(TEST_KEY, "Complaint Filed", reason="unit test")
        self.assertLess(new, before)  # complaint lowers trust
        # an audit row exists
        self.assertGreaterEqual(frappe.db.count("Customer Trust Log", {"customer": TEST_KEY}), 1)

    def test_health_bootstrap_insufficient_data(self):
        resolve_customer(TEST_PHONE_RAW, name="Test User")
        h = compute_health(TEST_KEY)
        # brand-new customer with no delivered+paid orders -> bootstrap
        self.assertEqual(h["band"], "Insufficient Data")
        self.assertIsNone(h["score"])

    def test_nba_open_complaint_is_critical(self):
        resolve_customer(TEST_PHONE_RAW, name="Test User")
        frappe.get_doc({
            "doctype": "Customer Complaint", "customer": TEST_KEY,
            "opened_at": frappe.utils.now_datetime(), "category": "Service",
            "severity": "High", "status": "Open", "description": "test",
        }).insert(ignore_permissions=True)
        r = compute_nba(TEST_KEY)
        self.assertEqual(r["action"], "Resolve Open Complaint")
        self.assertEqual(r["priority"], "Critical")

    def test_referral_gate_blocks_without_trust(self):
        resolve_customer(TEST_PHONE_RAW, name="Test User")
        from vitalvida.customer_relationship.referral import is_referral_eligible
        ok, reason = is_referral_eligible(TEST_KEY)
        self.assertFalse(ok)  # no delivered+paid order yet -> Law 7 blocks the ask


class TestCustomerSuccess(unittest.TestCase):
    def setUp(self):
        _cleanup()
        if not frappe.db.exists("Loop 4 Settings", "Loop 4 Settings"):
            frappe.get_doc({"doctype": "Loop 4 Settings"}).insert(ignore_permissions=True)

    def tearDown(self):
        _cleanup(); frappe.db.rollback()

    def test_open_complaint_forces_at_risk(self):
        resolve_customer(TEST_PHONE_RAW, name="Test User")
        frappe.get_doc({
            "doctype": "Customer Complaint", "customer": TEST_KEY,
            "opened_at": frappe.utils.now_datetime(), "category": "Service",
            "severity": "High", "status": "Open", "description": "t",
        }).insert(ignore_permissions=True)
        from vitalvida.customer_relationship.success import compute_success
        r = compute_success(TEST_KEY)
        self.assertEqual(r["state"], "At Risk")  # open complaint => At Risk

    def test_success_state_present_after_recompute(self):
        resolve_customer(TEST_PHONE_RAW, name="Test User")
        from vitalvida.customer_relationship.success import refresh_success
        r = refresh_success(TEST_KEY)
        self.assertIn(r["state"], ["On Track","Needs Attention","At Risk","Succeeded","Churned"])
        state = frappe.db.get_value("Customer Profile", TEST_KEY, "customer_success_state")
        self.assertEqual(state, r["state"])  # persisted to the profile field


class TestReviewsEngine(unittest.TestCase):
    def setUp(self):
        _cleanup()
        if not frappe.db.exists("Loop 4 Settings", "Loop 4 Settings"):
            frappe.get_doc({"doctype": "Loop 4 Settings"}).insert(ignore_permissions=True)

    def tearDown(self):
        for r in frappe.get_all("Customer Review", filters={"customer": TEST_KEY}, fields=["name"]):
            frappe.delete_doc("Customer Review", r["name"], force=True, ignore_permissions=True)
        for r in frappe.get_all("Customer Outcome", filters={"customer": TEST_KEY}, fields=["name"]):
            frappe.delete_doc("Customer Outcome", r["name"], force=True, ignore_permissions=True)
        _cleanup(); frappe.db.rollback()

    def test_positive_review_sets_positive_sentiment_and_raises_trust(self):
        resolve_customer(TEST_PHONE_RAW, name="Test User")
        before = frappe.db.get_value("Customer Profile", TEST_KEY, "trust_score") or 50
        from vitalvida.customer_relationship.reviews import record_review
        r = record_review(TEST_KEY, rating=5, review_text="Great!")
        self.assertEqual(r["sentiment"], "Positive")
        after = frappe.db.get_value("Customer Profile", TEST_KEY, "trust_score")
        self.assertGreaterEqual(after, before)  # positive review should not lower trust

    def test_negative_review_sentiment(self):
        resolve_customer(TEST_PHONE_RAW, name="Test User")
        from vitalvida.customer_relationship.reviews import record_review
        r = record_review(TEST_KEY, rating=1, review_text="Bad")
        self.assertEqual(r["sentiment"], "Negative")

    def test_review_request_blocked_without_success(self):
        resolve_customer(TEST_PHONE_RAW, name="Test User")
        from vitalvida.customer_relationship.reviews import should_request_review
        ok, reason = should_request_review(TEST_KEY)
        self.assertFalse(ok)  # no delivered+paid order -> don't ask yet


class TestAdvocacyEngine(unittest.TestCase):
    def setUp(self):
        _cleanup()
        if not frappe.db.exists("Loop 4 Settings", "Loop 4 Settings"):
            frappe.get_doc({"doctype": "Loop 4 Settings"}).insert(ignore_permissions=True)

    def tearDown(self):
        for r in frappe.get_all("Customer Advocacy", filters={"customer": TEST_KEY}, fields=["name"]):
            frappe.delete_doc("Customer Advocacy", r["name"], force=True, ignore_permissions=True)
        _cleanup(); frappe.db.rollback()

    def test_new_customer_not_advocacy_eligible(self):
        resolve_customer(TEST_PHONE_RAW, name="Test User")
        from vitalvida.customer_relationship.advocacy import refresh_advocacy
        r = refresh_advocacy(TEST_KEY)
        self.assertFalse(r["eligible"])  # brand-new, low trust -> not an advocate
        self.assertIn("reason", r)


class TestReferralRequiresOutcome(unittest.TestCase):
    """Change #3: referral now requires Outcome Achieved, not just trust + payment."""
    def setUp(self):
        _cleanup()
        if not frappe.db.exists("Loop 4 Settings", "Loop 4 Settings"):
            frappe.get_doc({"doctype": "Loop 4 Settings"}).insert(ignore_permissions=True)

    def tearDown(self):
        for r in frappe.get_all("Customer Outcome", filters={"customer": TEST_KEY}, fields=["name"]):
            frappe.delete_doc("Customer Outcome", r["name"], force=True, ignore_permissions=True)
        _cleanup(); frappe.db.rollback()

    def test_high_trust_but_no_outcome_still_blocks_referral(self):
        resolve_customer(TEST_PHONE_RAW, name="Test User")
        # force high trust + a delivered-paid count, but leave outcome Unknown
        frappe.db.set_value("Customer Profile", TEST_KEY,
            {"trust_score": 90, "delivered_paid_orders": 1, "outcome_status": "Unknown"})
        from vitalvida.customer_relationship.referral import is_referral_eligible
        ok, reason = is_referral_eligible(TEST_KEY)
        self.assertFalse(ok)  # trust is high, but outcome not achieved -> still blocked
        self.assertIn("Outcome", reason)
