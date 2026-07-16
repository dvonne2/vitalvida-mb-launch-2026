"""Package 16 — READ-ONLY Chart of Accounts drift audit.

ERPNext `Account` is the sole Chart of Accounts authority. This module NEVER
calls .insert(), .save() or otherwise modifies an Account. It compares the live
tree against a version-controlled expected structure (coa/expected_structure.json,
in source, not in the database) and records immutable drift evidence.

Any future CoA *application* engine must be a separate package with its own
approval, rollback and accounting review.
"""
import json
import pathlib

import frappe
from frappe.utils import now_datetime

from vitalvida.integration.idempotency import ensure_once, source_key
from vitalvida.governance.hashing import stable_hash

EXPECTED_FILE = pathlib.Path(__file__).resolve().parent / "expected_structure.json"
_KEYS = ("account_name", "parent_account", "account_type", "root_type", "is_group")


def expected_structure():
    return json.loads(EXPECTED_FILE.read_text())


def compare(company):
    """Pure read. Returns the drift report; writes nothing."""
    exp = expected_structure()
    accounts = exp.get("accounts") or []
    if not accounts:
        return {"company": company, "version": exp.get("version"),
                "configured": False, "missing": [], "extra": [], "mismatched": [],
                "drift_count": 0,
                "note": "expected structure is UNCONFIGURED; nothing compared"}

    live = {a["account_name"]: a for a in frappe.get_all(
        "Account", filters={"company": company},
        fields=["account_name", "parent_account", "account_type", "root_type", "is_group"])}

    missing, mismatched = [], []
    for want in accounts:
        got = live.get(want["account_name"])
        if not got:
            missing.append(want)
            continue
        diff = {k: {"expected": want.get(k), "live": got.get(k)}
                for k in _KEYS if k in want and want.get(k) not in (None, "")
                and got.get(k) != want.get(k)}
        if diff:
            mismatched.append({"account_name": want["account_name"], "diff": diff})

    expected_names = {a["account_name"] for a in accounts}
    extra = [n for n in live if n not in expected_names]

    return {"company": company, "version": exp.get("version"), "configured": True,
            "missing": missing, "extra": extra, "mismatched": mismatched,
            "drift_count": len(missing) + len(extra) + len(mismatched)}


def record_drift(company):
    """Record immutable drift evidence. Still creates/modifies NO Account."""
    report = compare(company)
    exp_hash = stable_hash(expected_structure().get("accounts") or [])
    key = source_key("COADRIFT", company, str(report["version"]), exp_hash,
                     now_datetime().strftime("%Y-%m-%dT%H:%M:%S"))
    res = ensure_once("COA Drift Event", {"source_key": key}, lambda: {
        "source_key": key, "company": company,
        "expected_version": str(report["version"]), "expected_hash": exp_hash,
        "drift_count": report["drift_count"],
        "missing_json": json.dumps(report["missing"], default=str),
        "extra_json": json.dumps(report["extra"], default=str),
        "mismatched_json": json.dumps(report["mismatched"], default=str),
        "audited_at": now_datetime(), "audited_by": frappe.session.user})
    return res["name"]
