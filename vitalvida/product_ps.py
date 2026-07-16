"""
vitalvida/product_ps.py — capture / restore the Product 'hidden' Property Setter
state, so Package 02 rollback can preserve a configuration that existed BEFORE
install. Single source of truth: the deploy CLI (deploy/product_ps_state.py) and
the tests both use these functions, so the tests exercise the real implementation.

State string format (one line):
    "ABSENT"    -> no Product 'hidden' Property Setter (or Product DocType absent)
    "VALUE=<v>" -> setter present with value <v>

`parse_state` validates BEFORE any DB mutation, so a corrupt/missing state can
never cause a partial or wrong change.
"""

import frappe

FILTERS = {"doc_type": "Product", "property": "hidden"}


def read_hidden_value():
    """Current value of the Product 'hidden' Property Setter, or None if absent
    (or the Product DocType does not exist). Read-only."""
    if not frappe.db.exists("DocType", "Product"):
        return None
    return frappe.db.get_value("Property Setter", FILTERS, "value")


def state_string():
    """Serialise the current state as 'ABSENT' or 'VALUE=<v>'. Read-only."""
    v = read_hidden_value()
    return "ABSENT" if v is None else f"VALUE={v}"


def parse_state(s):
    """('ABSENT', None) or ('VALUE', <v>). Raises ValueError on anything else —
    called before any mutation so bad input never touches the DB."""
    s = (s or "").strip()
    if s == "ABSENT":
        return ("ABSENT", None)
    if s.startswith("VALUE="):
        return ("VALUE", s.split("=", 1)[1])
    raise ValueError(f"unrecognised Product PS state: {s!r}")


def apply_state(state_str, commit=True):
    """Force the Product 'hidden' Property Setter to match `state_str` exactly.
    Validates first (raises ValueError before any DB write). ABSENT deletes the
    setter; VALUE=<v> ensures it exists with <v>. Commits when commit=True."""
    kind, value = parse_state(state_str)  # may raise BEFORE any mutation
    name = frappe.db.get_value("Property Setter", FILTERS, "name")
    if kind == "ABSENT":
        if name:
            frappe.delete_doc("Property Setter", name, force=True, ignore_permissions=True)
    else:
        frappe.make_property_setter({
            "doctype": "Product", "doctype_or_field": "DocType",
            "property": "hidden", "value": value, "property_type": "Check",
        }, is_system_generated=False)
    if commit:
        frappe.db.commit()


def write_state_file(path, s):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(s.strip() + "\n")


def read_state_file(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().strip()


def capture_to_file(path):
    """Write the current state to `path`; return the state string."""
    s = state_string()
    write_state_file(path, s)
    return s


def restore_from_file(path, commit=True):
    """Read `path` and apply it. Raises (no DB mutation) if the file is missing
    or its content is invalid."""
    apply_state(read_state_file(path), commit=commit)
