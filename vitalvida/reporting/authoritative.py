"""Package 13 Reporting — read-only projections over ERPNext authorities and
immutable domain events ONLY. Never reconstructs finance from VV Order statuses
(rule 3). No balance is stored here; every number is computed at read time from
Sales Invoice / Payment Entry / GL Entry.
"""
import frappe


def revenue_summary(company, from_date, to_date):
    return frappe.db.sql(
        """SELECT COALESCE(SUM(base_net_total),0) net_revenue,
                  COALESCE(SUM(base_total_taxes_and_charges),0) tax,
                  COALESCE(SUM(base_grand_total),0) invoiced
           FROM `tabSales Invoice`
           WHERE company=%s AND posting_date BETWEEN %s AND %s
             AND docstatus=1 AND is_return=0""",
        (company, from_date, to_date), as_dict=True)[0]


def collections_summary(company, from_date, to_date):
    return frappe.db.sql(
        """SELECT COALESCE(SUM(per.allocated_amount),0) collected
           FROM `tabPayment Entry Reference` per
           JOIN `tabPayment Entry` pe ON pe.name=per.parent
           WHERE pe.company=%s AND pe.posting_date BETWEEN %s AND %s
             AND pe.docstatus=1 AND per.reference_doctype='Sales Invoice'""",
        (company, from_date, to_date), as_dict=True)[0]


def account_balance(company, account, to_date):
    return frappe.db.sql(
        """SELECT COALESCE(SUM(debit-credit),0) balance
           FROM `tabGL Entry`
           WHERE company=%s AND account=%s AND posting_date<=%s
             AND is_cancelled=0""",
        (company, account, to_date), as_dict=True)[0]
