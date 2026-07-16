"""Package 17 — Affiliate Consequences: architecture tests.

SCOPE: ROOT is the WHOLE VitalVida app once installed. Never glob across it —
Package 10's Tax Band, Package 09's settlement wiring and Loop 5 all live there
and are not this package's to police. Assert only on PKG17_* below.

Assert on CODE, not prose: code_only() strips docstrings and comments so a
docstring describing the legacy bug cannot fail a test looking for it.
"""
import ast
import json
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

ROOT = Path(__file__).resolve().parents[1]

PKG17_MODULES = ("affiliate",)
PKG17_PATCH_DIR = "v17_0"
PKG17_DOCTYPES = ("affiliate_commission_event", "affiliate_payout_event",
                  "affiliate_payout_line")
PKG17_EVIDENCE = ("affiliate_commission_event", "affiliate_payout_event")


def package_py_files():
    files = []
    for d in PKG17_MODULES:
        p = ROOT / d
        if p.exists():
            files += sorted(p.rglob("*.py"))
    p = ROOT / "patches" / PKG17_PATCH_DIR
    if p.exists():
        files += sorted(p.rglob("*.py"))
    for slug in PKG17_DOCTYPES:
        f = ROOT / f"vitalvida/doctype/{slug}/{slug}.py"
        if f.exists():
            files.append(f)
    here = Path(__file__).resolve()
    return [f for f in files if f.resolve() != here]


def package_source():
    return "\n".join(f.read_text(errors="ignore") for f in package_py_files())


def code_only(src):
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return src
    doc_lines = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and body:
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                for ln in range(first.lineno, (first.end_lineno or first.lineno) + 1):
                    doc_lines.add(ln)
    return "\n".join(l for i, l in enumerate(src.splitlines(), 1)
                     if i not in doc_lines and not l.strip().startswith("#"))


class TestPackage17(FrappeTestCase):
    # ---- scope ----
    def test_scope_excludes_foreign_files(self):
        names = {f.name for f in package_py_files()}
        self.assertIn("commission.py", names)
        for foreign in ("install_settlement.py", "install_payroll_events.py",
                        "media_buyer.py", "tax_band.py", "da_dashboard.py"):
            self.assertNotIn(foreign, names, "test scope leaked outside Package 17")

    # ---- THE RULE: one event -> one writer -> one consequence ----
    def test_commission_has_exactly_one_consequence_writer(self):
        """We pay media buyers as AP, exactly like delivery agents and suppliers."""
        src = code_only(package_source())
        self.assertEqual(src.count('"doctype": "Purchase Invoice"'), 1,
                         "accrual is a Purchase Invoice, created in exactly one place")
        self.assertEqual(src.count('"doctype": "Payment Entry"'), 1)
        # affiliate never posts a raw Journal Entry: the invoice IS the payable
        self.assertNotIn('"doctype": "Journal Entry"', src)
        writers = (ROOT / "affiliate/consequences.py").read_text()
        self.assertIn("def post_commission_accrual", writers)
        self.assertIn("def post_payout_settlement", writers)

    def test_consequences_are_idempotent_and_linked(self):
        """Count CALLS, not prose. The docstring mentions link_consequence() too."""
        src = code_only((ROOT / "affiliate/consequences.py").read_text())
        self.assertEqual(src.count("ensure_once("), 2,
                         "exactly two idempotent postings: accrual and settlement")
        # one import line + two real calls
        self.assertEqual(src.count("link_consequence("), 2,
                         "each consequence must be linked back to its event")
        self.assertIn("vv_source_event_key", src)

    def test_every_writer_uses_the_spine(self):
        for mod in ("affiliate/commission.py", "affiliate/payout.py",
                    "affiliate/consequences.py"):
            self.assertIn("integration.idempotency", (ROOT / mod).read_text())

    # ---- no duplicate truth ----
    def test_commission_amount_is_not_recomputed_anywhere(self):
        """VV Order fields are a projection; the event is the authority."""
        src = code_only(package_source())
        # only _project_to_order / _project_to_orders may write the legacy fields
        self.assertEqual(src.count('"affiliate_commission_amount"'), 1)

    def test_payout_state_is_derived_not_trusted(self):
        src = (ROOT / "affiliate/payout.py").read_text()
        self.assertIn("def payout_state", src)
        self.assertIn("NOT EXISTS", src)  # unpaid derived, never cached

    def test_rule_version_is_snapshotted(self):
        src = (ROOT / "affiliate/commission.py").read_text()
        for token in ("rule_payload_hash", "rule_version", "rule_payload_json"):
            self.assertIn(token, src)

    def test_rule_resolution_fails_closed_on_ambiguity(self):
        src = (ROOT / "affiliate/commission.py").read_text()
        self.assertIn("Ambiguous affiliate commission rules", src)
        self.assertIn("Refusing to pick one", src)

    # ---- party accounting: the ledger answers "what do we owe" ----
    def test_both_consequences_resolve_the_same_supplier(self):
        """A Purchase Invoice carries the Supplier natively via `supplier`.
        Only the Payment Entry needs party_type/party. Both must resolve the
        SAME supplier, or the payment would settle a different party's payable."""
        src = code_only((ROOT / "affiliate/consequences.py").read_text())
        self.assertIn('"supplier": supplier', src, "the Purchase Invoice carries the party")
        self.assertEqual(src.count('"party_type": "Supplier"'), 1,
                         "only the Payment Entry needs party_type")
        self.assertEqual(src.count("resolve_supplier(event.media_buyer)"), 2,
                         "both writers must resolve the same supplier from the same event")

    def test_supplier_resolution_fails_closed(self):
        src = (ROOT / "affiliate/config.py").read_text()
        self.assertIn("def resolve_supplier", src)
        self.assertIn("has no Supplier party provisioned", src)
        self.assertIn("Refusing rather", src)

    def test_payable_is_read_from_the_party_ledger_not_recalculated(self):
        """The constitution: recorded once, available everywhere, never recalculated."""
        src = (ROOT / "affiliate/reports.py").read_text()
        self.assertIn("def outstanding_payable", src)
        self.assertIn("get_balance_on", src)
        self.assertIn('party_type="Supplier"', src)
        self.assertIn("ERPNext GL party balance", src)

    def test_ledger_and_events_are_reconcilable(self):
        src = (ROOT / "affiliate/reports.py").read_text()
        self.assertIn("def reconcile_payable", src)
        self.assertIn("reconciled", src)

    def test_patch_provisions_the_supplier_link(self):
        src = (ROOT / "patches/v17_0/install_affiliate_consequences.py").read_text()
        self.assertIn("_link_media_buyer_to_supplier", src)
        self.assertIn('"options": "Supplier"', src)

    # ---- fail closed, never guess an account ----
    def test_accounts_fail_closed(self):
        src = (ROOT / "affiliate/config.py").read_text()
        self.assertIn("affiliate_commission_expense_account", src)
        self.assertIn("affiliate_commission_payable_account", src)
        self.assertIn("Refusing rather than posting to a guessed account", src)

    def test_payout_requires_accrual_first(self):
        """Only commission with a submitted Purchase Invoice may be paid."""
        src = (ROOT / "affiliate/payout.py").read_text()
        # the payable query excludes anything not yet accrued
        self.assertIn("purchase_invoice IS NOT NULL", src)
        # and payout refuses outright when nothing is accrued
        self.assertIn("(Purchase Invoice posted) before it can be paid", src)

    def test_payout_enforces_separation_of_duties(self):
        self.assertIn("require_distinct_users", (ROOT / "affiliate/payout.py").read_text())

    # ---- the legacy hole is closed ----
    def test_legacy_guard_refuses_payment_without_consequence(self):
        src = (ROOT / "affiliate/legacy_guard.py").read_text()
        self.assertIn("def guard_payout_batch", src)
        self.assertIn("def guard_order_payout_status", src)
        self.assertIn("frappe.ValidationError", src)

    def test_legacy_guard_checks_the_current_consequence(self):
        """The guard must check what v1.2 actually writes, and that it is SUBMITTED.
        v1.2.0 shipped a guard still reading `journal_entry` — it would have
        rejected valid Purchase Invoice-backed accruals."""
        src = code_only((ROOT / "affiliate/legacy_guard.py").read_text())
        self.assertNotIn("journal_entry", src)
        self.assertIn("purchase_invoice", src)
        self.assertIn('"Purchase Invoice", accrued.purchase_invoice, "docstatus"', src)

    def test_no_raw_sql_updates_in_package17(self):
        """The legacy path used raw UPDATE to bypass controllers. We never do."""
        src = code_only(package_source()).upper()
        self.assertNotIn("UPDATE `TABVV ORDER`", src)

    # ---- evidence is immutable and service-written ----
    def test_evidence_is_immutable_and_service_written(self):
        for slug in PKG17_EVIDENCE:
            d = json.load(open(ROOT / f"vitalvida/doctype/{slug}/{slug}.json"))
            for perm in d.get("permissions", []):
                self.assertFalse(perm.get("create"), d["name"])
                self.assertFalse(perm.get("write"), d["name"])
                self.assertFalse(perm.get("delete"), d["name"])
            src = (ROOT / f"vitalvida/doctype/{slug}/{slug}.py").read_text()
            self.assertIn("guard_immutable", src)
            self.assertIn("guard_no_delete", src)

    def test_patch_creates_no_money(self):
        src = (ROOT / f"patches/{PKG17_PATCH_DIR}/install_affiliate_consequences.py").read_text()
        for dt in ("Journal Entry", "Payment Entry", "GL Entry", "Affiliate Payout Batch"):
            self.assertNotIn(f'"doctype": "{dt}"', src)
        self.assertNotIn("frappe.db.commit", src)


def assert_safe():
    """Runtime safety proof: no affiliate money exists outside the ledger."""
    from vitalvida.affiliate.reports import orders_paid_without_event, unaccrued_commission
    orphans = orders_paid_without_event()
    assert not orphans, (f"{len(orphans)} VV Orders marked affiliate-Paid with no "
                         f"authoritative payout event: {orphans[:5]}")
    unaccrued = unaccrued_commission()
    assert not unaccrued, (f"{len(unaccrued)} commission events with no Purchase "
                           f"Invoice — the buyer is not owed in the ledger: {unaccrued[:5]}")
    live = frappe.get_all("Event Consumer Map",
                          filters={"consumer_module": ["like", "vitalvida.affiliate%"]})
    assert not live, f"Package 17 wires no consumers; found {live}"
    print("safe: 0 orders paid without a consequence, 0 unaccrued commission, "
          "0 affiliate consumers")
    print("OK")
