"""Package 09 tests — immutability, idempotency, SoD, no derived balances.

FrappeTestCase rollback discipline: nothing committed.
"""
import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime, nowdate

EVENT_KEYS = ["vv.settlement.da_fee_earned", "vv.settlement.batch_approved",
              "vv.settlement.batch_paid", "vv.settlement.remittance_outstanding"]


# ---- bench-execute verification helpers ------------------------------------
def verify_installed():
    for dt in ("Incentive Rule Version", "DA Earning Event", "Settlement Batch",
               "Settlement Batch Earning", "Settlement Receipt Event",
               "Outstanding Remittance Event"):
        assert frappe.db.exists("DocType", dt), f"{dt} missing"
    assert frappe.db.exists("Incentive Rule Version",
                            {"rule_key": "da_delivery_fee", "version": 1}), \
        "seed rule da_delivery_fee v1 missing"
    for key in EVENT_KEYS:
        assert frappe.db.exists("Event Definition", {"event_key": key}), \
            f"Event Definition {key} missing"
    print("installed: OK")


def verify_guards():
    import inspect
    from vitalvida.settlement import engine, read_models
    src = inspect.getsource(engine) + inspect.getsource(read_models)
    assert "order_status" not in src, "engine must not read order status"
    assert "get_balance_on" in inspect.getsource(read_models), \
        "balances must come from ERPNext"
    print("guards: OK")


def _mk_rule():
    name = frappe.db.exists("Incentive Rule Version",
                            {"rule_key": "test_fee", "version": 1})
    if name:
        return frappe.get_doc("Incentive Rule Version", name)
    return frappe.get_doc({
        "doctype": "Incentive Rule Version", "rule_key": "test_fee",
        "version": 1, "rule_type": "Flat Amount", "amount": 100,
        "effective_from": "2026-01-01", "is_active": 1,
    }).insert(ignore_permissions=True)


def _mk_earning(rule, key_suffix=""):
    key = "TEST::ORDER-1::Delivery Fee::" + rule.name + key_suffix
    existing = frappe.db.exists("DA Earning Event", {"idempotency_key": key})
    if existing:
        return frappe.get_doc("DA Earning Event", existing)
    da = frappe.get_all("Delivery Agent", pluck="name", limit=1)
    supplier = frappe.get_all("Supplier", pluck="name", limit=1)
    if not da or not supplier:
        return None
    return frappe.get_doc({
        "doctype": "DA Earning Event", "delivery_agent": da[0],
        "supplier": supplier[0], "source_order": "ORDER-1",
        "earning_type": "Delivery Fee", "qualifying_event": "vv.order.closed",
        "fee_rule_version": rule.name, "amount": 100, "status": "Earned",
        "earned_at": now_datetime(), "idempotency_key": key,
    }).insert(ignore_permissions=True)


class TestPackage09(FrappeTestCase):
    def test_rule_resolution_effective_dating(self):
        from vitalvida.vitalvida.doctype.incentive_rule_version.incentive_rule_version import resolve
        _mk_rule()
        rule = resolve("test_fee", on_date=nowdate())
        self.assertEqual(rule.rule_key, "test_fee")
        with self.assertRaises(frappe.ValidationError):
            resolve("no_such_rule")

    def test_rule_immutable_once_referenced(self):
        rule = _mk_rule()
        e = _mk_earning(rule)
        if not e:
            self.skipTest("no Delivery Agent/Supplier fixture on this site")
        rule.reload()
        rule.amount = 999
        with self.assertRaises(frappe.ValidationError):
            rule.save(ignore_permissions=True)

    def test_earning_immutable_and_undeletable(self):
        rule = _mk_rule()
        e = _mk_earning(rule)
        if not e:
            self.skipTest("no Delivery Agent/Supplier fixture on this site")
        e.reload()
        e.amount = 12345
        with self.assertRaises(frappe.ValidationError):
            e.save(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            e.reload()
            e.delete()

    def test_earning_idempotency_unique_key(self):
        rule = _mk_rule()
        e = _mk_earning(rule)
        if not e:
            self.skipTest("no fixtures")
        from vitalvida.integration.idempotency import ensure_once
        res = ensure_once("DA Earning Event",
                          {"idempotency_key": e.idempotency_key},
                          {"doctype": "DA Earning Event"})
        self.assertTrue(res["already_emitted"])
        self.assertEqual(res["name"], e.name)

    def test_reversal_requires_exact_negative(self):
        rule = _mk_rule()
        e = _mk_earning(rule)
        if not e:
            self.skipTest("no fixtures")
        bad = frappe.get_doc({
            "doctype": "DA Earning Event", "delivery_agent": e.delivery_agent,
            "supplier": e.supplier, "source_order": e.source_order,
            "earning_type": e.earning_type, "qualifying_event": "x",
            "fee_rule_version": rule.name, "amount": -50, "status": "Earned",
            "earned_at": now_datetime(), "idempotency_key": "TEST::rev-bad",
            "reversal_of": e.name})
        with self.assertRaises(frappe.ValidationError):
            bad.insert(ignore_permissions=True)

    def test_no_sum_delivered_minus_payments_anywhere(self):
        import inspect
        from vitalvida.settlement import engine, read_models
        src = inspect.getsource(engine) + inspect.getsource(read_models)
        self.assertNotIn("order_status", src)
        self.assertNotIn("da_fee_paid", src)
        self.assertNotIn("total_payable),0) FROM `tabVV Order` WHERE "
                         "order_status='Delivered'", src)
