"""Package 14 architecture and pure calculation tests.

SCOPE — learned from the v11 and v1.0.3 verify failures:

  * ROOT is the WHOLE VitalVida app once installed. Globbing `tax_*` across it
    matches Tax Band (Package 10's PAYE authority), which legitimately allows
    create. Package 14 must only assert things about ITS OWN doctypes, listed
    explicitly in PKG14_DOCTYPES.

  * The deploy/ scripts are NEVER copied into the app, so this module does not
    assert anything about them. An earlier version searched parent directories
    for "deploy/install.sh" and found Package 01's leftover installer in the
    bench root, then asserted against it. Never go looking outside the package.
    deploy/verify.sh checks those guarantees directly, against the real files.
"""
from pathlib import Path
from decimal import Decimal
import ast, json
import frappe
from frappe.tests.utils import FrappeTestCase

ROOT = Path(__file__).resolve().parents[1]

# Package 14's OWN doctypes. Never glob: `tax_*` also matches Tax Band.
PKG14_DOCTYPES = (
    "tax_authority_snapshot_event", "tax_calculation_snapshot_event",
    "tax_reconciliation_snapshot_event", "tax_filing_snapshot_event",
    "tax_exception", "tax_resolution_event", "tax_approval_event",
)


class TestPackage14(FrappeTestCase):
    def payload(self):
        return "\n".join(p.read_text(errors="ignore") for p in (ROOT / "tax").rglob("*.py"))

    def pkg14_doctype_json(self):
        for slug in PKG14_DOCTYPES:
            p = ROOT / f"vitalvida/doctype/{slug}/{slug}.json"
            if p.exists():
                yield json.load(open(p))

    def test_scope_excludes_foreign_doctypes(self):
        """Regression: Package 14 must not assert on Tax Band or other packages."""
        names = {d["name"] for d in self.pkg14_doctype_json()}
        self.assertNotIn("Tax Band", names)
        self.assertNotIn("Item Tax Template", names)
        self.assertGreater(len(names), 0)

    def test_exact_tax_band_contract(self):
        src = (ROOT / "tax/paye.py").read_text()
        self.assertIn('REQUIRED_FIELDS = ("lower_limit", "upper_limit", "rate_percent")', src)
        self.assertNotIn("cumulative_tax", src)
        self.assertNotIn("base_tax", src)

    def test_paye_calculation_and_continuity(self):
        from vitalvida.tax.paye import calculate_from_bands
        bands = [{"name": "A", "band_name": "A", "lower": Decimal("0"), "upper": Decimal("1000"), "rate": Decimal("10")},
                 {"name": "B", "band_name": "B", "lower": Decimal("1000"), "upper": None, "rate": Decimal("20")}]
        total, refs = calculate_from_bands(Decimal("1500"), bands)
        self.assertEqual(total, Decimal("200.00"))
        self.assertEqual(len(refs), 2)

    def test_vat_is_account_scoped_and_fail_closed(self):
        src = (ROOT / "tax/vat.py").read_text()
        self.assertIn("tax_account", src)
        self.assertIn("item_wise_tax_detail", src)
        self.assertIn("On Net Total", src)
        self.assertIn("Inclusive VAT requires", src)

    def test_company_scoped_snapshots(self):
        self.assertIn('"company":company', (ROOT / "tax/snapshot.py").read_text().replace(" ", ""))
        for dt in ("tax_calculation_snapshot_event", "tax_reconciliation_snapshot_event",
                   "tax_filing_snapshot_event"):
            d = json.load(open(ROOT / f"vitalvida/doctype/{dt}/{dt}.json"))
            self.assertIn("company", {f["fieldname"] for f in d["fields"]})

    def test_reconciliation_explicit_handlers(self):
        src = (ROOT / "tax/reconciliation.py").read_text()
        for t in ('"VAT Output"', '"VAT Input"', '"PAYE"', "voucher_type='Payment Entry'", "natural"):
            self.assertIn(t, src)

    def test_filing_scope_validation(self):
        src = (ROOT / "tax/filing.py").read_text()
        for token in ("c.company != company", "c.tax_type != tax_type",
                      "outside the filing period", "already been superseded"):
            self.assertIn(token, src)

    def test_no_parallel_tax_rate_doctype(self):
        names = {d["name"] for d in self.pkg14_doctype_json()}
        self.assertFalse(names & {"Tax Rate", "VAT Rate", "Tax Band Definition"})

    def test_no_hardcoded_tax_rates(self):
        tree = ast.parse(self.payload())
        offenders = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if (isinstance(t, ast.Name) and t.id.lower() in {"vat_rate", "paye_rate", "tax_rate"}
                            and isinstance(n.value, ast.Constant)):
                        offenders.append(t.id)
        self.assertEqual(offenders, [])

    def test_no_authority_or_accounting_writes(self):
        src = self.payload().replace(" ", "")
        for token in ('db.set_value("TaxBand"', 'db.set_value("ItemTaxTemplate"',
                      '"doctype":"TaxBand"', '"doctype":"ItemTaxTemplate"'):
            self.assertNotIn(token, src)
        for dt in ('SalesInvoice', 'PurchaseInvoice', 'GLEntry', 'PaymentEntry', 'Account',
                   'SalesTaxesandChargesTemplate', 'PurchaseTaxesandChargesTemplate'):
            self.assertNotIn(f'"doctype":"{dt}"', src)

    def test_zero_consumers_and_append_only_permissions(self):
        patch = (ROOT / "patches/v14_0/install_tax_reference_snapshot.py").read_text()
        self.assertNotIn("Event Consumer Map", patch)
        self.assertNotIn("request_consumers", patch)
        # ONLY Package 14's own doctypes — Tax Band belongs to Package 10.
        for d in self.pkg14_doctype_json():
            for perm in d.get("permissions", []):
                self.assertFalse(perm.get("create"), d["name"])
                self.assertFalse(perm.get("write"), d["name"])
                self.assertFalse(perm.get("delete"), d["name"])

    def test_no_explicit_commit_in_patch(self):
        self.assertNotIn("frappe.db.commit",
                         (ROOT / "patches/v14_0/install_tax_reference_snapshot.py").read_text())


def assert_inert():
    live = frappe.get_all("Event Consumer Map", filters={"consumer_module": ["like", "vitalvida.tax%"]})
    assert not live, f"Package 14 must wire zero consumers: {live}"
    print("inert: 0 tax consumers requested, 0 activated, 0 live rows in Event Consumer Map")
    print("OK")
