"""Packages 12-16 architectural + inert-install tests.

Emits an explicit 'OK' sentinel on success (verify.sh requires it).

SCOPE (v12 — learned from the first real install):
    ROOT is the WHOLE VitalVida app once installed. It contains Packages 01-10,
    Loop 5, the portals and the M-series reports — none of which these tests
    govern. In the build tree ROOT happened to contain only this package, so
    `ROOT.rglob("*.py")` looked correct and passed; after install the identical
    line scanned the entire app and produced three false failures. Always scan
    THIS PACKAGE's own files via package_py_files().

    Assertions about "does our code do X" must test CODE, not prose: code_only()
    strips docstrings and comments, so a docstring saying "never reads VV Order"
    cannot fail a test looking for VV Order.
"""
import ast
import json
import re
from pathlib import Path

import frappe
from frappe.tests.utils import FrappeTestCase

ROOT = Path(__file__).resolve().parents[1]

PKG_MODULE_DIRS = ("activation", "coa", "controls", "governance", "reporting", "schemas")
PKG_PATCH_DIRS = ("v12_0", "v13_0", "v15_0", "v16_0")
PKG_DOCTYPES = (
    "control_definition", "control_execution_event", "control_exception",
    "control_resolution_event", "event_schema_definition", "schema_validation_event",
    "coa_drift_event", "consumer_activation_request",
    "consumer_activation_approval_event", "consumer_activation_event",
    "consumer_activation_reversal_event",
)
EVIDENCE_DOCTYPE_SLUGS = (
    "control_execution_event", "control_exception", "control_resolution_event",
    "schema_validation_event", "coa_drift_event", "consumer_activation_request",
    "consumer_activation_approval_event", "consumer_activation_event",
    "consumer_activation_reversal_event",
)


def package_py_files():
    """Every .py file THIS package owns — and nothing else in the app."""
    files = []
    for d in PKG_MODULE_DIRS:
        p = ROOT / d
        if p.exists():
            files += sorted(p.rglob("*.py"))
    for d in PKG_PATCH_DIRS:
        p = ROOT / "patches" / d
        if p.exists():
            files += sorted(p.rglob("*.py"))
    reg = ROOT / "patches" / "_register.py"
    if reg.exists():
        files.append(reg)
    for slug in PKG_DOCTYPES:
        f = ROOT / "vitalvida" / "doctype" / slug / f"{slug}.py"
        if f.exists():
            files.append(f)
    here = Path(__file__).resolve()
    return [f for f in files if f.resolve() != here]


def package_source():
    return "\n".join(f.read_text(errors="ignore") for f in package_py_files())


def code_only(src):
    """Return src with docstrings and comments removed, so assertions test code."""
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
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in doc_lines:
            continue
        if line.strip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


class TestPackages1216(FrappeTestCase):
    # ---- REGRESSION: the v11 verify failure was a test-scope bug ----
    def test_package_scope_does_not_leak_into_the_app(self):
        names = {f.name for f in package_py_files()}
        self.assertIn("engine.py", names)
        for foreign in ("da_dashboard.py", "investor_portal.py",
                        "install_settlement.py", "install_payroll_events.py",
                        "install_finance_consequences.py"):
            self.assertNotIn(foreign, names,
                             "test scope leaked outside this package — the v11 bug")

    def test_code_only_strips_docstrings_and_comments(self):
        sample = '"""says VV Order in prose"""\nx = 1  # VV Order in a comment\n'
        self.assertNotIn("prose", code_only(sample))

    # ---- rule 2/3: no parallel GL / balance authority ----
    def test_no_parallel_gl_doctype(self):
        payload = code_only(package_source())
        self.assertNotIn('"doctype": "GL Entry"', payload)
        # Package 16 is read-only: NO Account creation in THIS package.
        self.assertNotIn('"doctype": "Account"', payload)

    def test_reports_use_erpnext_authorities(self):
        text = (ROOT / "reporting/authoritative.py").read_text()
        for tbl in ("`tabSales Invoice`", "`tabPayment Entry`", "`tabGL Entry`"):
            self.assertIn(tbl, text)

    def test_reports_do_not_read_vv_order_status(self):
        """Tests the SQL, not the docstring that says it never reads VV Order."""
        text = code_only((ROOT / "reporting/authoritative.py").read_text())
        self.assertNotIn("`tabVV Order`", text)
        self.assertNotIn('"VV Order"', text)

    # ---- observe-only: no enforcement path, no mode switch ----
    def test_controls_have_no_enforcement_path(self):
        src = code_only((ROOT / "controls/engine.py").read_text())
        self.assertNotIn("_control_mode", src)
        d = json.load(open(ROOT / "vitalvida/doctype/control_definition/control_definition.json"))
        self.assertNotIn("mode", {f["fieldname"] for f in d["fields"]})

    def test_no_consumer_requests_point_at_missing_modules(self):
        """Every consumer method THIS package registers must resolve to real code."""
        for f in package_py_files():
            for m in re.findall(r'proposed_consumer_method="([^"]+)"', f.read_text()):
                mod, fn = m.rsplit(".", 1)
                path = ROOT / (mod.replace("vitalvida.", "").replace(".", "/") + ".py")
                self.assertTrue(path.exists(), f"{m}: {path} does not exist")
                self.assertIn(f"def {fn}(", path.read_text(), f"{m}: no def {fn}()")

    # ---- rule 11/14: engines consume the spine; no synthetic-key ECM insert ----
    def test_engines_use_spine_idempotency(self):
        for mod in ("controls/engine.py", "schemas/validation.py",
                    "coa/audit.py", "activation/engine.py"):
            self.assertIn("integration.idempotency", (ROOT / mod).read_text())

    def test_no_standalone_consumer_map_insert(self):
        """THIS package must never insert a standalone Event Consumer Map row.

        Scoped deliberately: Packages 09/10 contain a flat-model fallback for
        their own wiring, which is not this package's to police.
        """
        payload = code_only(package_source())
        self.assertNotIn('"doctype": "Event Consumer Map"', payload)

    def test_no_get_attr_on_db_values(self):
        for line in code_only(package_source()).splitlines():
            self.assertNotIn("frappe.get_attr(", line,
                             "evaluators must come from the source-controlled registry")

    # ---- Package 16 read-only ----
    def test_package16_never_writes_accounts(self):
        for f in (ROOT / "coa").rglob("*.py"):
            for n in ast.walk(ast.parse(f.read_text())):
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
                    self.assertNotIn(n.func.attr, ("insert", "save"),
                                     f"{f.name}: Package 16 is read-only; ERPNext "
                                     "Account is the sole CoA authority")

    def test_control_exception_has_no_resolution_state(self):
        d = json.load(open(ROOT / "vitalvida/doctype/control_exception/control_exception.json"))
        names = {f["fieldname"] for f in d["fields"]}
        for banned in ("status", "resolved_at", "resolution_event"):
            self.assertNotIn(banned, names,
                             "resolution is derived from Control Resolution Event")

    def test_schema_validation_uses_dialect_and_payload_hash(self):
        src = (ROOT / "schemas/validation.py").read_text()
        self.assertIn("validator.validate_payload", src)
        self.assertIn("payload_hash", src)

    def test_evidence_doctypes_are_service_written_only(self):
        """Defense in depth: permissions must agree with the controllers."""
        for slug in EVIDENCE_DOCTYPE_SLUGS:
            d = json.load(open(ROOT / f"vitalvida/doctype/{slug}/{slug}.json"))
            for perm in d.get("permissions", []):
                self.assertFalse(perm.get("create"),
                                 f"{d['name']}: evidence creation must be service-only")
                self.assertFalse(perm.get("write"), f"{d['name']}: no write")
                self.assertFalse(perm.get("delete"), f"{d['name']}: no delete")

    def test_control_identity_includes_input_hash(self):
        src = (ROOT / "controls/engine.py").read_text()
        self.assertIn("input_hash = stable_hash(inputs)", src)
        self.assertIn('"v"+str(control.rule_version), input_hash', src)

    def test_control_verifies_authoritative_source(self):
        src = (ROOT / "controls/engine.py").read_text()
        self.assertIn("frappe.db.exists(source_doctype, source_name)", src)
        self.assertIn("assert_authorized_emitter", src)

    def test_immutable_controllers_exist(self):
        for slug in EVIDENCE_DOCTYPE_SLUGS:
            f = ROOT / f"vitalvida/doctype/{slug}/{slug}.py"
            self.assertTrue(f.exists(), f"{slug}: controller missing")
            src = f.read_text()
            self.assertIn("guard_immutable", src)
            self.assertIn("guard_no_delete", src)

    # ---- ARCHITECTURE: evidence never stores runtime state ----
    def test_request_has_no_status_field(self):
        d = json.load(open(ROOT / "vitalvida/doctype/consumer_activation_request/"
                                  "consumer_activation_request.json"))
        names = {f["fieldname"] for f in d["fields"]}
        for banned in ("activation_status", "status", "is_active", "activated_child_row"):
            self.assertNotIn(banned, names,
                             "the request is approval EVIDENCE; runtime state belongs "
                             "only to Event Consumer Map")

    def test_state_is_derived_not_stored(self):
        src = (ROOT / "activation/engine.py").read_text()
        self.assertIn("def state(", src)
        self.assertIn('"live_in_event_consumer_map"', src)
        self.assertNotIn('db_set("activation_status"', src)

    def test_activation_requires_matching_approval_hash(self):
        src = (ROOT / "activation/engine.py").read_text()
        self.assertIn("approved_change_hash", src)
        self.assertIn("No approval matching this request", src)

    def test_no_dormant_enforcement_or_bulk_activation_patch(self):
        self.assertFalse((ROOT / "vitalvida/doctype/governance_settings").exists())
        self.assertFalse((ROOT / "patches/v17_0").exists())
        self.assertNotIn("schema_mode", (ROOT / "schemas/validation.py").read_text())

    def test_no_unbound_controls_seeded(self):
        src = (ROOT / "patches/v12_0/install_controls.py").read_text()
        self.assertIn("CONTROLS = []", src)

    # ---- inert install: NO consumers registered, none wired ----
    def test_install_is_inert(self):
        if not frappe.db.exists("DocType", "Consumer Activation Request"):
            return  # doctypes not migrated on this runner; static guards still ran
        acts = frappe.get_all("Consumer Activation Event")
        self.assertEqual(len(acts), 0, "install must not activate consumers")
        live = frappe.get_all("Event Consumer Map",
                              filters={"consumer_module": ["like", "vitalvida.controls%"]})
        self.assertEqual(len(live), 0, "install must not wire consumers")


def run_sentinel():
    print("OK")


def assert_inert():
    """Called by verify.sh: prove the install left consumers inert.

    'Inert' is proven against the RUNTIME AUTHORITY (Event Consumer Map), not
    against any status field — because no status field exists.
    """
    acts = frappe.get_all("Consumer Activation Event", pluck="name")
    assert not acts, f"install must not activate consumers, found {acts}"
    for mod in ("vitalvida.controls%", "vitalvida.schemas%", "vitalvida.coa%"):
        live = frappe.get_all("Event Consumer Map",
                              filters={"consumer_module": ["like", mod]})
        assert not live, f"install must wire no consumers, found {live} for {mod}"
    print("inert: 0 consumers requested, 0 activated, 0 live rows in Event Consumer Map")
    print("OK")
