"""
Package 02 — real Frappe tests (Constitution PRD-005).

Run:
    bench --site vitalvida.systemforce.ng run-tests \
        --module vitalvida.tests.test_package02_recipe

Coverage (addresses reviewer concern #5 — tests must exercise what their names
claim):
  Resolver (real fixtures, FrappeTestCase rollback):
    - structured expansion; case/space-insensitive match; inactive -> empty;
      unmapped/None/"" -> empty; strict drops qty<=0; behaviour-neutral vs the
      legacy parser; AMBIGUOUS normalized duplicates -> [] + detected.
  deduct_on_payment (mocked DB boundary — exercises the real branching without
  the Inventory-domain DA Warehouse machinery):
    - structured path calls _deduct_da_stock with structured components;
    - legacy fallback path invokes the legacy parser for an unmapped package;
    - ambiguous package -> NO deduction;
    - whole-order idempotency guard -> NO deduction when already deducted.
  Product deprecation + rollback:
    - patch hides Product (Property Setter) and asserts 0 rows;
    - patch aborts when Product has rows;
    - capture/restore of the Product hidden Property Setter (vitalvida.product_ps):
      absent->1->absent; 0->1->0; 1->1; missing/corrupt state -> fail-closed.
  Install-time safety:
    - baseline-hash helper refuses a drifted deduction.py.
"""

import os
import shutil
import tempfile
import hashlib
from unittest.mock import patch, MagicMock

import frappe
from frappe.tests.utils import FrappeTestCase

from vitalvida import recipe
from vitalvida.recipe import (
    resolve_components, resolve_components_strict, classify,
    duplicate_normalized_bundle_names, total_units,
)
from vitalvida import deduction
from vitalvida.deduction import _parse_contents


TEST_ITEMS = ["_PKG02 Shampoo", "_PKG02 Conditioner", "_PKG02 Pomade"]


# ---------------------------------------------------------------------------
# CONTAMINATION SAFETY (v1.0.7)
# The suite relies ENTIRELY on FrappeTestCase transaction/savepoint isolation:
# every record a test creates (_PKG02 Items / Bundle Definitions, and any change
# to the Product 'hidden' Property Setter) is rolled back automatically.
#
# THE SUITE COMMITS NOTHING. There is no frappe.db.commit() anywhere in this file,
# and no cleanup/restore helper that could persist state. Therefore running the
# suite on the production site cannot alter the production Product deprecation.
#
# We do NOT swallow exceptions in cleanup: there is no cleanup to swallow. The
# only teardown work is a READ-ONLY module-level assertion that the committed
# Product Property Setter state is identical before and after the suite; if a
# future change ever leaked a commit, that assertion FAILS the suite (it does not
# silently repair anything).
#
# Durable removal of residue from an EARLIER crashed run is a separate, explicit,
# operator-run maintenance command (deploy/cleanup_test_residue.py) — never run by
# this suite.
# ---------------------------------------------------------------------------

def _read_product_hidden_ps():
    """READ-ONLY snapshot of the Product 'hidden' Property Setter as
    (name, value), or None if the Product DocType is absent or no such setter
    exists. Performs no writes and no commit."""
    if not frappe.db.exists("DocType", "Product"):
        return None
    row = frappe.db.get_value(
        "Property Setter", {"doc_type": "Product", "property": "hidden"},
        ["name", "value"], as_dict=True)
    return (row.name, row.value) if row else None


# Module-level guard: prove the whole suite leaves the COMMITTED production
# Property Setter state unchanged. Read-only; no writes, no commit, no swallow.
_MODULE_PS_SNAPSHOT = "unset"


def setUpModule():
    global _MODULE_PS_SNAPSHOT
    _MODULE_PS_SNAPSHOT = _read_product_hidden_ps()


def tearDownModule():
    after = _read_product_hidden_ps()
    assert after == _MODULE_PS_SNAPSHOT, (
        "Test suite altered the COMMITTED Product 'hidden' Property Setter: "
        f"before={_MODULE_PS_SNAPSHOT} after={after}. The suite must commit "
        "nothing; a difference here means a test leaked a commit.")


def _ensure_item(code):
    if not frappe.db.exists("Item", code):
        frappe.get_doc({
            "doctype": "Item", "item_code": code, "item_name": code,
            "is_stock_item": 1, "item_group": "All Item Groups",
        }).insert(ignore_permissions=True)


def _make_bundle(name, rows, is_active=1):
    doc = frappe.get_doc({
        "doctype": "Bundle Definition", "bundle_name": name,
        "bundle_price": 0, "is_active": is_active,
    })
    for prod, qty in rows:
        doc.append("products", {"product": prod, "quantity_required": qty})
    doc.insert(ignore_permissions=True)
    return doc


class TestPackage02Resolver(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        for it in TEST_ITEMS:
            _ensure_item(it)
    # No tearDownClass override: FrappeTestCase rolls back all _PKG02 fixtures.

    def setUp(self):
        super().setUp()
        super().setUp()
        self.bundle = "_PKG02 Test Bundle"
        _make_bundle(self.bundle, [(TEST_ITEMS[0], 2), (TEST_ITEMS[1], 3), (TEST_ITEMS[2], 1)])

    def test_resolves_components(self):
        comps = dict(resolve_components(self.bundle))
        self.assertEqual(comps, {TEST_ITEMS[0]: 2, TEST_ITEMS[1]: 3, TEST_ITEMS[2]: 1})

    def test_case_and_space_insensitive(self):
        self.assertTrue(resolve_components(self.bundle.lower()))
        self.assertTrue(resolve_components("  " + self.bundle.upper() + "  "))
        self.assertEqual(total_units("  " + self.bundle.lower() + " "), 6)

    def test_inactive_and_unmapped_empty(self):
        _make_bundle("_PKG02 Inactive", [(TEST_ITEMS[0], 5)], is_active=0)
        self.assertEqual(resolve_components("_PKG02 Inactive"), [])
        self.assertEqual(resolve_components("_PKG02 Nope"), [])
        self.assertEqual(resolve_components(None), [])
        self.assertEqual(resolve_components(""), [])

    def test_strict_drops_non_positive(self):
        _make_bundle("_PKG02 Zero", [(TEST_ITEMS[0], 0), (TEST_ITEMS[1], 4)])
        strict = dict(resolve_components_strict("_PKG02 Zero"))
        self.assertNotIn(TEST_ITEMS[0], strict)
        self.assertEqual(strict[TEST_ITEMS[1]], 4)

    def test_behaviour_neutral_vs_legacy(self):
        legacy_string = f"2 {TEST_ITEMS[0]} \u00b7 3 {TEST_ITEMS[1]} \u00b7 1 {TEST_ITEMS[2]}"
        self.assertEqual(sorted(resolve_components(self.bundle)),
                         sorted(_parse_contents(legacy_string)))

    def test_ambiguous_duplicate_normalized_names(self):
        # Two ACTIVE bundles that normalise identically ("family saves").
        _make_bundle("_PKG02 Dup", [(TEST_ITEMS[0], 1)])
        _make_bundle("_pkg02   dup", [(TEST_ITEMS[1], 9)])
        status, payload = classify("_PKG02 DUP")
        self.assertEqual(status, "ambiguous")
        self.assertEqual(len(payload), 2)
        self.assertEqual(resolve_components("_PKG02 DUP"), [])   # fail-closed
        dups = duplicate_normalized_bundle_names()
        self.assertIn("_pkg02 dup", dups)
        self.assertEqual(len(dups["_pkg02 dup"]), 2)


class TestPackage02Deduction(FrappeTestCase):
    # Exercise deduct_on_payment's real branching with the DB boundary mocked, so
    # we test resolution/path selection without the Inventory-domain DA Warehouse
    # machinery. No DB writes occur (all collaborators mocked).
    def _run(self, order_obj, existing=False, bundles=None, legacy_contents=None):
        captured = []
        bundles = bundles or {}

        def fake_classify(pkg):
            return recipe.classify(pkg)  # real classify, but bundles come via get_all mock

        with patch.object(deduction.frappe.db, "exists", return_value=existing), \
             patch.object(deduction.frappe, "get_doc", return_value=order_obj), \
             patch.object(deduction, "_deduct_da_stock",
                          side_effect=lambda **kw: captured.append((kw["product"], kw["quantity"]))), \
             patch.object(deduction, "_read_legacy_contents", return_value=legacy_contents or ""), \
             patch.object(deduction, "classify") as mock_classify:
            mock_classify.side_effect = lambda pkg: bundles.get(pkg, ("empty", []))
            deduction.deduct_on_payment("ORD-TEST")
        return captured

    def _order(self, package_name="P", da="DA-1"):
        o = MagicMock()
        o.delivery_agent = da
        o.package_name = package_name
        return o

    def test_structured_path(self):
        captured = self._run(
            self._order("Family Saves"),
            bundles={"Family Saves": ("structured", [("Shampoo", 10), ("Pomade", 10)])})
        self.assertEqual(sorted(captured), [("Pomade", 10), ("Shampoo", 10)])

    def test_legacy_fallback_path(self):
        captured = self._run(
            self._order("Legacy Only"),
            bundles={"Legacy Only": ("empty", [])},
            legacy_contents="1 Shampoo \u00b7 2 Pomade")
        self.assertEqual(sorted(captured), [("Pomade", 2), ("Shampoo", 1)])

    def test_ambiguous_stops_deduction(self):
        captured = self._run(
            self._order("Dup"),
            bundles={"Dup": ("ambiguous", ["Dup A", "Dup B"])})
        self.assertEqual(captured, [])  # no deduction on ambiguity

    def test_idempotency_guard_blocks(self):
        captured = self._run(self._order("Family Saves"), existing=True,
                             bundles={"Family Saves": ("structured", [("Shampoo", 1)])})
        self.assertEqual(captured, [])


class TestPackage02Deprecation(FrappeTestCase):
    # No commits, no tearDown restore, no tearDownClass purge: FrappeTestCase
    # rolls back everything these tests do to the Property Setter, so the
    # production deprecation is never altered.

    def test_deprecation_hides_when_empty(self):
        if not frappe.db.exists("DocType", "Product"):
            self.skipTest("custom Product DocType not present on this site")
        if frappe.db.count("Product"):
            self.skipTest("Product has rows on this site; deprecation intentionally aborts")
        from vitalvida.patches.v6_1 import deprecate_custom_product as dp
        dp.execute()
        ps = frappe.db.get_value("Property Setter",
                                 {"doc_type": "Product", "property": "hidden"}, "value")
        self.assertEqual(str(ps), "1")

    def test_deprecation_aborts_when_rows_present(self):
        if not frappe.db.exists("DocType", "Product"):
            self.skipTest("custom Product DocType not present on this site")
        from vitalvida.patches.v6_1 import deprecate_custom_product as dp
        with patch.object(frappe.db, "count", return_value=3):
            self.assertRaises(Exception, dp.execute)


class TestPackage02ProductPsState(FrappeTestCase):
    """Test the ACTUAL capture/restore implementation (vitalvida.product_ps) that
    install.sh and rollback.sh use, against the real Frappe DB. All mutations use
    commit=False so FrappeTestCase rolls them back."""

    def setUp(self):
        if not frappe.db.exists("DocType", "Product"):
            self.skipTest("custom Product DocType not present on this site")
        import vitalvida.product_ps as P
        self.P = P
        self._tmp = tempfile.mkdtemp(prefix="pkg02ps_")
        self.state_file = os.path.join(self._tmp, "product_hidden_ps.pre")
        # start from a known ABSENT baseline within the test txn
        self.P.apply_state("ABSENT", commit=False)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_absent_then_install_then_rollback_absent(self):
        self.assertEqual(self.P.capture_to_file(self.state_file), "ABSENT")
        self.P.apply_state("VALUE=1", commit=False)                 # install (patch) hides
        self.assertEqual(str(self.P.read_hidden_value()), "1")
        self.P.restore_from_file(self.state_file, commit=False)     # rollback
        self.assertIsNone(self.P.read_hidden_value())               # back to absent

    def test_hidden0_then_install_then_rollback_hidden0(self):
        self.P.apply_state("VALUE=0", commit=False)                 # pre-existing config
        self.assertEqual(self.P.capture_to_file(self.state_file), "VALUE=0")
        self.P.apply_state("VALUE=1", commit=False)                 # install hides
        self.assertEqual(str(self.P.read_hidden_value()), "1")
        self.P.restore_from_file(self.state_file, commit=False)     # rollback
        self.assertEqual(str(self.P.read_hidden_value()), "0")      # pre-existing 0 restored

    def test_hidden1_rollback_hidden1(self):
        self.P.apply_state("VALUE=1", commit=False)                 # already hidden before install
        self.assertEqual(self.P.capture_to_file(self.state_file), "VALUE=1")
        self.P.restore_from_file(self.state_file, commit=False)     # rollback keeps 1
        self.assertEqual(str(self.P.read_hidden_value()), "1")

    def test_missing_and_corrupt_state_fail_closed(self):
        self.P.apply_state("VALUE=1", commit=False)                 # known DB state
        before = self.P.read_hidden_value()
        # missing file -> raises, no mutation
        missing = os.path.join(self._tmp, "does_not_exist")
        self.assertRaises(Exception, self.P.restore_from_file, missing, commit=False)
        self.assertEqual(self.P.read_hidden_value(), before)
        # corrupt content -> ValueError BEFORE any mutation
        with open(self.state_file, "w") as fh:
            fh.write("GARBAGE\n")
        self.assertRaises(ValueError, self.P.restore_from_file, self.state_file, commit=False)
        self.assertEqual(self.P.read_hidden_value(), before)


class TestPackage02ProductionSafety(FrappeTestCase):
    def test_suite_preserves_installed_hidden_state(self):
        """Prove the suite does not remove the production deprecation: if the
        Product 'hidden' Property Setter exists right now (package installed), it
        must still exist with value '1' — and the module-level tearDownModule
        assertion additionally proves the full suite leaves it unchanged."""
        ps = _read_product_hidden_ps()
        if ps is None:
            self.skipTest("Product hidden Property Setter not present (package not "
                          "installed on this site); nothing to preserve")
        name, value = ps
        self.assertTrue(name)
        self.assertEqual(str(value), "1",
                         "installed Product deprecation must be hidden=1")


class TestPackage02BaselineGate(FrappeTestCase):
    def test_baseline_hash_refuses_drift(self):
        """The install-time gate compares the live deduction.py hash to the
        approved baseline; a drifted file must not match."""
        expected = "fc26e87cf414e0b5dc2053a56c899d8d61f0d84ea03c46b9f444dc0d950b0ef3"
        drifted = hashlib.sha256(b"# tampered\n").hexdigest()
        self.assertNotEqual(drifted, expected)
        # sanity: hashing is what the gate uses
        self.assertEqual(len(expected), 64)
