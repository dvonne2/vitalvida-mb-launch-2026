"""Link a VitalVida domain event to the standard ERPNext consequence it creates.

Constitution GOV-004 (reference the ERPNext consequence; hold no competing
balance) and the classification\'s rule 7 (every custom event references the
ERPNext document it creates). Package 01 provides the field spec + linker;
later packages install the fields on their bucket-B doctypes.
"""
import frappe

# Custom-field spec later packages install on a bucket-B domain record so it can
# point at its ERPNext consequence without duplicating any balance.
CONSEQUENCE_FIELD_SPEC = [
    {"fieldname": "consequence_doctype", "fieldtype": "Data",
     "label": "ERPNext Consequence DocType", "read_only": 1},
    {"fieldname": "consequence_name", "fieldtype": "Data",
     "label": "ERPNext Consequence", "read_only": 1},
    {"fieldname": "consequence_posted", "fieldtype": "Check",
     "label": "Consequence Posted", "read_only": 1, "default": "0"},
]


def make_consequence_custom_fields(domain_doctype: str) -> dict:
    """Return a ``create_custom_fields`` mapping for a bucket-B domain doctype."""
    return {domain_doctype: [dict(s) for s in CONSEQUENCE_FIELD_SPEC]}


def link_consequence(domain_doc, consequence_doctype: str, consequence_name: str):
    """Attach the ERPNext consequence to a domain event, guarding re-link.

    Refuses to silently repoint an event at a *different* consequence (that would
    hide a double-posting). Re-linking to the same one is a safe no-op.
    """
    cur_dt = domain_doc.get("consequence_doctype")
    cur_nm = domain_doc.get("consequence_name")
    if cur_nm and (cur_dt, cur_nm) != (consequence_doctype, consequence_name):
        frappe.throw(
            f"{domain_doc.doctype} {domain_doc.name} already links consequence "
            f"{cur_dt} {cur_nm}; refusing to repoint to {consequence_doctype} "
            f"{consequence_name}. Reverse the first consequence explicitly.")
    domain_doc.db_set("consequence_doctype", consequence_doctype)
    domain_doc.db_set("consequence_name", consequence_name)
    domain_doc.db_set("consequence_posted", 1)
