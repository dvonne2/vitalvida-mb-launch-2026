"""Package 08 tests — writers are idempotent, guarded, single-authority.

FrappeTestCase rollback discipline: tests commit nothing (Package 02 standard).
Also exposes bench-execute verify_* helpers used by deploy/verify.sh.
"""
import frappe
from frappe.tests.utils import FrappeTestCase

from vitalvida.finance import config as fin_config
from vitalvida.integration.idempotency import ensure_once, source_key

EVENT_KEYS = ["vv.finance.payment_confirmed", "vv.order.closed",
              "vv.finance.liability_approved",
              "vv.finance.profit_first_allocated"]


# ---- bench-execute verification helpers (read-only) -----------------------
def verify_installed():
    assert frappe.db.exists("DocType", "VV Finance Config"), "config doctype missing"
    for dt in ("Payment Entry", "Sales Invoice", "Journal Entry"):
        assert frappe.get_meta(dt).has_field("vv_source_event_key"), \
            f"{dt} lacks vv_source_event_key"
    print("installed: OK")


def verify_events():
    for key in EVENT_KEYS:
        assert frappe.db.exists("Event Definition", {"event_key": key}), \
            f"Event Definition {key} missing"
    print("events: OK")


def verify_writers_guarded():
    """Writers must refuse to post with an incomplete account map (R126)."""
    cfg = frappe.get_doc("VV Finance Config")
    if not cfg.get("company"):
        try:
            fin_config.get_config()
            raise AssertionError("get_config did not fail on empty config")
        except frappe.ValidationError:
            pass
    print("writer guard: OK")


# ---- unit tests ------------------------------------------------------------
class TestPackage08(FrappeTestCase):
    def test_source_key_deterministic(self):
        a = source_key("vv.order.closed", "Order Closure Event", "OCE-0001")
        b = source_key("vv.order.closed", "Order Closure Event", "OCE-0001")
        self.assertEqual(a, b)
        self.assertIn("::", a)

    def test_ensure_once_is_idempotent(self):
        """Two calls with the same unique filters create exactly one row."""
        if not frappe.db.exists("DocType", "Integration Outbox"):
            self.skipTest("Package 01 Integration Outbox not installed")
        key = frappe.generate_hash(length=10)
        filters = {"event_key": "test.pkg08", "source_name": key,
                   "consumer_method": "x.y.z"}
        values = dict(filters, source_doctype="Note", status="Pending")
        first = ensure_once("Integration Outbox", filters, values)
        second = ensure_once("Integration Outbox", filters, values)
        self.assertTrue(first["created"])
        self.assertTrue(second["already_emitted"])
        self.assertEqual(first["name"], second["name"])

    def test_config_validation_reports_missing(self):
        cfg = frappe.get_doc("VV Finance Config")
        problems = fin_config.validate_accounts(cfg)
        if not cfg.get("company"):
            self.assertTrue(problems)

    def test_read_models_source_declarations(self):
        """Every read model declares its authority + date (REP-008)."""
        import inspect
        from vitalvida.finance import read_models
        src = inspect.getsource(read_models)
        self.assertNotIn("tabVV Order", src,
                         "read models must never touch VV Order (GOV-003)")
        for authority in ("Payment Entry", "GL Entry"):
            self.assertIn(authority, src)

    def test_no_stored_balance_in_package(self):
        import inspect
        from vitalvida.finance import consequences, profit_first_gl
        for mod in (consequences, profit_first_gl):
            src = inspect.getsource(mod)
            self.assertNotIn("current_balance", src,
                             f"{mod.__name__} must not read/write stored balances")

    def test_closure_writer_verifies_conditions(self):
        from vitalvida.finance.consequences import _assert_closure_conditions

        class Stub:
            name = "OCE-TEST"
            _vals = {"delivery_completed": 1, "payment_confirmed": 0}
            meta = frappe._dict(has_field=lambda f: f in Stub._vals)

            def get(self, k, d=None):
                return self._vals.get(k, d)

        with self.assertRaises(frappe.ValidationError):
            _assert_closure_conditions(Stub())
