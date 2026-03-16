"""M28 — Payroll Analytics. Summary view for Finance with month-on-month trend."""
import frappe

def execute(filters=None):
    columns = [
        {"fieldname":"payroll_month","label":"Month","fieldtype":"Date","width":120},
        {"fieldname":"status","label":"Status","fieldtype":"Data","width":100},
        {"fieldname":"employee_count","label":"Employees","fieldtype":"Int","width":100},
        {"fieldname":"total_gross","label":"Total Gross","fieldtype":"Currency","width":140},
        {"fieldname":"total_deductions","label":"Total Deductions","fieldtype":"Currency","width":140},
        {"fieldname":"total_net","label":"Total Net","fieldtype":"Currency","width":140},
    ]

    runs = frappe.get_all("Monthly Payroll Run",
        fields=["payroll_month","status","total_gross","total_deductions","total_net"],
        order_by="payroll_month desc", limit=12)

    data = []
    for r in runs:
        emp_count = frappe.db.sql("""
            SELECT COUNT(*) FROM `tabPayslip` WHERE parent = (
                SELECT name FROM `tabMonthly Payroll Run` WHERE payroll_month = %s LIMIT 1
            )
        """, (r.payroll_month,))[0][0] or 0
        data.append({
            "payroll_month": r.payroll_month,
            "status": r.status,
            "employee_count": emp_count,
            "total_gross": float(r.total_gross or 0),
            "total_deductions": float(r.total_deductions or 0),
            "total_net": float(r.total_net or 0),
        })
    return columns, data
