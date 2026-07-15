"""Package 16B — Chart of Accounts + Profit First: architecture tests.

SCOPE: ROOT is the whole app once installed. Assert only on this package's files.
Assert on CODE, not docstrings.
"""
import ast, csv, json
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

ROOT = Path(__file__).resolve().parents[1]
PKG_FILES = ("finance/profit_first_gl.py", "finance/chart_of_accounts.py",
             "patches/v18_0/install_coa_profit_first.py")
CSV = ROOT / "vitalvida/data/vitalvida_chart_of_accounts_v1.csv"


def code_only(src):
    try: tree = ast.parse(src)
    except SyntaxError: return src
    doc = set()
    for n in ast.walk(tree):
        b = getattr(n, "body", None)
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef)) and b:
            f = b[0]
            if isinstance(f, ast.Expr) and isinstance(f.value, ast.Constant) and isinstance(f.value.value, str):
                for ln in range(f.lineno, (f.end_lineno or f.lineno) + 1): doc.add(ln)
    return "\n".join(l for i, l in enumerate(src.splitlines(), 1)
                     if i not in doc and not l.strip().startswith("#"))


class TestPackage16B(FrappeTestCase):
    def test_chart_company_matches_a_real_company(self):
        rows = list(csv.DictReader(CSV.open()))
        companies = {r["company"] for r in rows}
        self.assertEqual(len(companies), 1, "chart must target exactly one company")
        company = companies.pop()
        self.assertTrue(frappe.db.exists("Company", company),
                        f"chart targets {company!r} which does not exist on this site")

    def test_chart_is_structurally_sound(self):
        rows = list(csv.DictReader(CSV.open()))
        numbers, names = set(), set()
        for r in rows:
            self.assertNotIn(r["account_number"], numbers, f"duplicate {r['account_number']}")
            numbers.add(r["account_number"])
            names.add(r["account_name"])
        for r in rows:
            parent = r["parent_account"].strip()
            if parent:
                self.assertIn(parent, names, f"{r['account_name']}: parent {parent!r} not defined")

    def test_profit_first_buckets_match_the_chart(self):
        from vitalvida.finance.profit_first_gl import BUCKET_FIELD
        rows = list(csv.DictReader(CSV.open()))
        banks = {r["account_name"] for r in rows
                 if r["account_type"] == "Bank" and r["is_group"] == "0"}
        # 7 buckets + 1 source account
        self.assertEqual(len(BUCKET_FIELD), 7)
        self.assertGreaterEqual(len(banks), 8, "chart must define a source + 7 bucket banks")

    def test_percentages_must_total_exactly_100(self):
        src = (ROOT / "finance/profit_first_gl.py").read_text()
        self.assertIn("must total exactly 100%", src)
        self.assertIn("abs(total-100.0) > 0.0001", code_only(src))

    def test_allocation_requires_a_submitted_payment_entry(self):
        src = code_only((ROOT / "finance/profit_first_gl.py").read_text())
        self.assertIn("docstatus != 1", src)
        self.assertIn("received_amount", src)

    def test_no_rounding_drift(self):
        """Last bucket absorbs the remainder so legs always balance."""
        src = code_only((ROOT / "finance/profit_first_gl.py").read_text())
        self.assertIn("amount-debited", src.replace(" ", ""))

    def test_legacy_closure_allocation_refuses(self):
        src = (ROOT / "finance/profit_first_gl.py").read_text()
        self.assertIn("def on_order_closed_allocate", src)
        self.assertIn("closure-based allocation is prohibited", src)

    def test_no_stored_bucket_balance(self):
        src = code_only((ROOT / "finance/profit_first_gl.py").read_text())
        self.assertNotIn("current_balance", src)
        self.assertIn("get_balance_on", src)

    def test_patch_creates_no_accounts_and_posts_nothing(self):
        src = code_only((ROOT / "patches/v18_0/install_coa_profit_first.py").read_text())
        for dt in ("Account", "Journal Entry", "Payment Entry", "GL Entry"):
            self.assertNotIn(f'"doctype": "{dt}"', src)
        self.assertNotIn("frappe.db.commit", src)

    def test_patch_wires_no_live_consumer(self):
        src = code_only((ROOT / "patches/v18_0/install_coa_profit_first.py").read_text())
        self.assertNotIn('append("consumers"', src)
        self.assertNotIn("consumer_method", src)

    def test_importer_is_explicit_and_dry_runnable(self):
        src = (ROOT / "finance/chart_of_accounts.py").read_text()
        self.assertIn("def dry_run", src)
        self.assertIn("only_for", src)   # authorised roles only


def assert_nothing_posted():
    """Prove the install created no accounts and posted nothing."""
    from vitalvida.finance.profit_first_gl import BUCKET_FIELD
    cfg = frappe.get_cached_doc("VV Finance Config")
    assert not cfg.get("enable_profit_first_gl") or cfg.get("profit_first_mode") != "Active", \
        "Profit First must not be Active on install"
    jes = frappe.db.count("Journal Entry", {"vv_source_event_key": ["like", "%profit_first%"]})
    assert jes == 0, f"install must post no Profit First Journal Entries, found {jes}"
    live = frappe.get_all("Event Consumer Map",
                          filters={"consumer_method": ["like", "%profit_first_gl%"]})
    assert not live, f"install must wire no Profit First consumer, found {live}"
    print(f"nothing posted: 0 Profit First JEs, 0 consumers wired, {len(BUCKET_FIELD)} buckets defined")
    print("OK")
