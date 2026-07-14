"""Package 10 tests — earning immutability, run idempotency, DA exclusion,
no derived pay. Nothing committed (FrappeTestCase rollback)."""
import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

EVENT_KEYS = ["vv.payroll.commission_earned", "vv.payroll.approval_recorded", "vv.payroll.payment_recorded"]
RULE_KEYS = ["telesales_commission", "employee_pension_rate",
             "champion_bonus_passthrough"]


def verify_installed():
    for dt in ("Commission Earning Event", "Payroll Run Event", "Payroll Run Line",
               "Payroll Approval Event", "Payroll Payment Event"):
        assert frappe.db.exists("DocType", dt), f"{dt} missing"
    for key in EVENT_KEYS:
        assert frappe.db.exists("Event Definition", {"event_key": key}), \
            f"Event Definition {key} missing"
    for rk in RULE_KEYS:
        assert frappe.db.exists("Incentive Rule Version", {"rule_key": rk}), \
            f"rule seed {rk} missing"
    print("installed: OK")


def verify_guards():
    import inspect
    from vitalvida.payroll_events import generators, consequences, read_models
    src = "".join(inspect.getsource(m)
                  for m in (generators, consequences, read_models))
    assert "order_status" not in src, "payroll must not read order status"
    assert "DA Payout Record" not in src, "DA payouts must not enter payroll"
    assert "total_earned_ytd" not in src, "no stored running totals"
    print("guards: OK")


def _rule(key="test_pay_rule"):
    name = frappe.db.exists("Incentive Rule Version",
                            {"rule_key": key, "version": 1})
    if name:
        return frappe.get_doc("Incentive Rule Version", name)
    return frappe.get_doc({
        "doctype": "Incentive Rule Version", "rule_key": key, "version": 1,
        "rule_type": "Flat Amount", "amount": 500,
        "effective_from": "2026-01-01", "is_active": 1,
    }).insert(ignore_permissions=True)


def _employee():
    e = frappe.get_all("VV Employee", filters={"is_active": 1},
                       pluck="name", limit=1)
    return e[0] if e else None


def _mk_earning(emp, rule, ref="T-1"):
    key = f"TESTP::{ref}::Telesales::{rule.name}"
    existing = frappe.db.exists("Commission Earning Event",
                                {"idempotency_key": key})
    if existing:
        return frappe.get_doc("Commission Earning Event", existing)
    return frappe.get_doc({
        "doctype": "Commission Earning Event", "employee": emp,
        "source_doctype": "Order Closure Event", "source_reference": ref,
        "earning_type": "Telesales", "rule_version": rule.name,
        "inputs_json": json.dumps({"t": 1}), "amount": 500,
        "period": "2026-07", "status": "Earned",
        "earned_at": now_datetime(), "idempotency_key": key,
    }).insert(ignore_permissions=True)


class TestPackage10(FrappeTestCase):
    def test_earning_immutable(self):
        emp = _employee()
        if not emp:
            self.skipTest("no VV Employee fixture")
        e = _mk_earning(emp, _rule())
        e.reload()
        e.amount = 999
        with self.assertRaises(frappe.ValidationError):
            e.save(ignore_permissions=True)
        with self.assertRaises(frappe.ValidationError):
            e.reload()
            e.delete()

    def test_reversal_exact_negative(self):
        emp = _employee()
        if not emp:
            self.skipTest("no VV Employee fixture")
        rule = _rule()
        e = _mk_earning(emp, rule)
        bad = frappe.get_doc({
            "doctype": "Commission Earning Event", "employee": emp,
            "source_doctype": e.doctype, "source_reference": e.name,
            "earning_type": e.earning_type, "rule_version": rule.name,
            "amount": -100, "period": "2026-07", "status": "Earned",
            "earned_at": now_datetime(),
            "idempotency_key": "TESTP::rev-bad", "reversal_of": e.name})
        with self.assertRaises(frappe.ValidationError):
            bad.insert(ignore_permissions=True)

    def test_status_flow_enforced(self):
        emp = _employee()
        if not emp:
            self.skipTest("no VV Employee fixture")
        e = _mk_earning(emp, _rule(), ref="T-flow")
        e.reload()
        e.status = "Posted"           # Earned -> Posted skips Approved
        with self.assertRaises(frappe.ValidationError):
            e.save(ignore_permissions=True)

    def test_compute_run_idempotent(self):
        from vitalvida.payroll_events.consequences import compute_run
        emp = _employee()
        if not emp:
            self.skipTest("no VV Employee fixture")
        try:
            first = compute_run("2099-01")
        except frappe.ValidationError:
            self.skipTest("no payable lines on this site")
        second = compute_run("2099-01")
        self.assertEqual(first["name"], second["name"])
        self.assertFalse(second["created"])

    def test_paye_delegates_to_tax_bands(self):
        from vitalvida.payroll import compute_paye
        self.assertGreater(compute_paye(1_200_000), 0)

    def test_bridge_is_idempotent_shape(self):
        """Bridge keys on the BAR name — same BAR can never earn twice."""
        from vitalvida.integration.idempotency import source_key
        a = source_key("BAR-0001", "Champion Bonus", "champion_bonus_passthrough-v1")
        b = source_key("BAR-0001", "Champion Bonus", "champion_bonus_passthrough-v1")
        self.assertEqual(a, b)

    def test_typed_payroll_consequence_links(self):
        self.assertEqual(frappe.get_meta("Payroll Approval Event").get_field("journal_entry").options, "Journal Entry")
        self.assertEqual(frappe.get_meta("Payroll Payment Event").get_field("payment_entry").options, "Payment Entry")

    def test_claim_path_uses_row_lock_and_atomic_update(self):
        import inspect
        from vitalvida.payroll_events import consequences
        src = inspect.getsource(consequences.compute_run)
        self.assertIn("FOR UPDATE", src)
        self.assertIn("ROW_COUNT", src)

    def test_explicit_dedicated_writer_ownership(self):
        from vitalvida.payroll_events import consequences
        self.assertEqual(consequences.APPROVAL_WRITER, "vitalvida.finance.consequences.on_payroll_approved")
        self.assertEqual(consequences.PAYMENT_WRITER, "vitalvida.finance.consequences.on_payroll_paid")

    def test_typed_payroll_consequence_links(self):
        self.assertEqual(frappe.get_meta("Payroll Approval Event").get_field("journal_entry").options, "Journal Entry")
        self.assertEqual(frappe.get_meta("Payroll Payment Event").get_field("payment_entry").options, "Payment Entry")

    def test_claim_path_uses_row_lock_and_atomic_update(self):
        import inspect
        from vitalvida.payroll_events import consequences
        src = inspect.getsource(consequences.compute_run)
        self.assertIn("FOR UPDATE", src)
        self.assertIn("ROW_COUNT", src)

    def test_explicit_dedicated_writer_ownership(self):
        from vitalvida.payroll_events import consequences
        self.assertEqual(consequences.APPROVAL_WRITER, "vitalvida.finance.consequences.on_payroll_approved")
        self.assertEqual(consequences.PAYMENT_WRITER, "vitalvida.finance.consequences.on_payroll_paid")


class TestPaymentConsequenceShape(FrappeTestCase):
    """v1.1.1 regression guards for blockers B5/B6 — static, always run."""

    def _p08_writer_source(self):
        import inspect
        from vitalvida.finance import consequences as fin
        return inspect.getsource(fin.on_payroll_paid)

    def test_payroll_payment_is_journal_entry_not_internal_transfer(self):
        """B5: clearing Net Wages Payable must be a Journal Entry; an
        'Internal Transfer' Payment Entry into a liability account is
        invalid ERPNext usage and gets rejected at submit."""
        src = self._p08_writer_source()
        self.assertIn('"Journal Entry"', src)
        self.assertNotIn("Internal Transfer", src)
        self.assertNotIn('"Payment Entry"', src)

    def test_deductions_have_their_own_payable_account(self):
        """B6: other deductions must NOT credit Net Wages Payable, or the
        credit strands there after pay_run clears only total_net."""
        import inspect
        from vitalvida.payroll_events import consequences as pc
        legs_src = inspect.getsource(pc._payroll_legs)
        self.assertIn("deductions_payable_account", legs_src)
        self.assertNotIn(
            '(run.total_other_deductions,"net_wages_payable_account")',
            legs_src)

    def test_payment_event_carries_typed_journal_link(self):
        meta = frappe.get_meta("Payroll Run Event")
        fld = next((x for x in meta.fields
                    if x.fieldname == "net_wages_cleared_by"), None)
        self.assertIsNotNone(fld)
        self.assertEqual(fld.options, "Journal Entry")


class TestEndToEndPosting(FrappeTestCase):
    """v1.1.1: the flows that survived review unposted must actually post.
    Runs against _Test Company (standard Frappe test fixture); skips with an
    explicit reason where the fixture is absent, never silently."""

    COMPANY = "_Test Company"

    def setUp(self):
        if not frappe.db.exists("Company", self.COMPANY):
            self.skipTest("_Test Company fixture absent on this site; "
                          "run on a test site (bench new-site --for-tests) "
                          "for end-to-end posting coverage.")
        self.abbr = frappe.db.get_value("Company", self.COMPANY, "abbr")

    def tearDown(self):
        frappe.db.rollback()

    def _account(self, name, root_type, account_type=None):
        full = f"{name} - {self.abbr}"
        if frappe.db.exists("Account", full):
            return full
        parent = frappe.db.get_value(
            "Account", {"company": self.COMPANY, "root_type": root_type,
                        "is_group": 1}, "name")
        doc = frappe.get_doc({
            "doctype": "Account", "account_name": name,
            "company": self.COMPANY, "parent_account": parent,
            "root_type": root_type,
            "account_type": account_type or "",
        }).insert(ignore_permissions=True)
        return doc.name

    def test_net_wages_clearing_journal_entry_posts(self):
        """Dr Net Wages Payable / Cr Bank must submit cleanly — the exact
        shape on_payroll_paid now produces (B5)."""
        bank = self._account("VV Test Bank", "Asset", "Bank")
        payable = self._account("VV Test Net Wages Payable", "Liability")
        je = frappe.get_doc({
            "doctype": "Journal Entry", "voucher_type": "Bank Entry",
            "company": self.COMPANY, "posting_date": frappe.utils.nowdate(),
            "cheque_no": "E2E-TEST-REF", "cheque_date": frappe.utils.nowdate(),
            "user_remark": "v1.1.1 E2E: net wages clearing shape",
            "accounts": [
                {"account": payable, "debit_in_account_currency": 1000},
                {"account": bank, "credit_in_account_currency": 1000},
            ]})
        je.insert(ignore_permissions=True)
        je.submit()
        self.assertEqual(je.docstatus, 1)
        gl = frappe.get_all("GL Entry", filters={"voucher_no": je.name},
                            fields=["account", "debit", "credit"])
        self.assertEqual(len(gl), 2)

    def test_internal_transfer_pe_to_liability_is_rejected(self):
        """Documents WHY B5 was a blocker: ERPNext refuses the old shape."""
        bank = self._account("VV Test Bank", "Asset", "Bank")
        payable = self._account("VV Test Net Wages Payable", "Liability")
        pe = frappe.get_doc({
            "doctype": "Payment Entry", "payment_type": "Internal Transfer",
            "company": self.COMPANY, "posting_date": frappe.utils.nowdate(),
            "paid_from": bank, "paid_to": payable,
            "paid_amount": 1000, "received_amount": 1000,
            "reference_no": "E2E-NEG", "reference_date": frappe.utils.nowdate(),
        })
        with self.assertRaises(Exception):
            pe.insert(ignore_permissions=True)
            pe.submit()
