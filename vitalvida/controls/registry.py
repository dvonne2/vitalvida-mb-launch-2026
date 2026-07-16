"""Source-controlled control evaluators.

SECURITY: Control Definition stores an evaluator CODE, never a Python path.
Nothing here is resolved from a database value, so write access to Control
Definition cannot become arbitrary code execution. Adding an evaluator is a
reviewed source change, not a data edit.

Each evaluator: (source_doctype, source_name, config) -> (result, message)
where result is "Pass" or "Fail". Evaluators MUST be read-only.
"""
import frappe

EVALUATORS = {}


def evaluator(code):
    def _reg(fn):
        EVALUATORS[code] = fn
        return fn
    return _reg


def get(code):
    fn = EVALUATORS.get(code)
    if not fn:
        frappe.throw(f"Unknown control evaluator code {code!r}. Evaluators are "
                     "source-controlled; add one in vitalvida/controls/registry.py.")
    return fn


def codes():
    return sorted(EVALUATORS)


@evaluator("record_exists.v1")
def _record_exists(source_doctype, source_name, config):
    ok = bool(frappe.db.exists(source_doctype, source_name))
    return ("Pass", "") if ok else ("Fail", f"{source_doctype} {source_name} does not exist")


@evaluator("field_equals.v1")
def _field_equals(source_doctype, source_name, config):
    field = config.get("fieldname")
    if not field:
        frappe.throw("field_equals.v1 requires config.fieldname")
    expected = config.get("expected")
    actual = frappe.db.get_value(source_doctype, source_name, field)
    ok = actual == expected
    return ("Pass", "") if ok else ("Fail", f"{field}: expected {expected!r}, found {actual!r}")


@evaluator("finance_document_submitted.v1")
def _finance_document_submitted(source_doctype, source_name, config):
    allowed = {"Sales Invoice", "Purchase Invoice", "Payment Entry", "Journal Entry"}
    if source_doctype not in allowed:
        return ("Fail", f"{source_doctype} is not an approved ERPNext finance document")
    if frappe.db.get_value(source_doctype, source_name, "docstatus") != 1:
        return ("Fail", "finance consequence is not submitted")
    return ("Pass", "")


@evaluator("no_negative_stock.v1")
def _no_negative_stock(source_doctype, source_name, config):
    n = frappe.db.count("Bin", {"actual_qty": ["<", 0]})
    return ("Pass", "") if not n else ("Fail", f"{n} Bin rows have negative stock")
