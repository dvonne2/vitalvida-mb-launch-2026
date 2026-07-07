"""
M28 — HR, Payroll & Staff Records Engine
payroll.py

run_monthly_payroll() — creates Monthly Payroll Run with payslips for all active employees
compute_paye() — Nigerian income tax band calculation
Supports dry_run mode (returns results without saving).
Integrates M26 Salary Deductions into net pay.
"""

import frappe
from frappe.utils import now_datetime, getdate, add_months, get_first_day


def run_monthly_payroll(payroll_month: str, dry_run: bool = False) -> dict:
    """
    Compute payroll for all active VV Employees for the given month.
    If dry_run=True: returns full results without creating any records.
    """
    from vitalvida.vitalvida.doctype.vv_commission_settings.vv_commission_settings import (
        get_commission_settings
    )

    month_start = str(get_first_day(getdate(payroll_month)))
    month_end = str(add_months(getdate(month_start), 1))

    try:
        settings = get_commission_settings()
    except Exception:
        settings = None

    employees = frappe.get_all(
        "VV Employee",
        filters={"is_active": 1},
        fields=["name", "employee_name", "employment_type", "staff_role",
                "base_salary", "commission_eligible", "linked_da", "linked_closer"]
    )

    payslips = []
    total_gross = 0.0
    total_deductions = 0.0
    total_net = 0.0

    for emp in employees:
        slip = _compute_payslip(emp, month_start, month_end, settings)
        payslips.append(slip)
        total_gross += slip["gross_pay"]
        total_deductions += (
            slip["paye_deduction"] + slip["pension_deduction"]
            + slip["other_deductions"] + slip["salary_deductions_m26"]
        )
        total_net += slip["net_pay"]

    result = {
        "payroll_month": month_start,
        "employee_count": len(payslips),
        "total_gross": round(total_gross, 2),
        "total_deductions": round(total_deductions, 2),
        "total_net": round(total_net, 2),
        "payslips": payslips,
        "dry_run": dry_run,
    }

    if dry_run:
        return result

    # Create Monthly Payroll Run
    try:
        run = frappe.get_doc({
            "doctype": "Monthly Payroll Run",
            "payroll_month": month_start,
            "status": "Processing",
            "total_gross": result["total_gross"],
            "total_deductions": result["total_deductions"],
            "total_net": result["total_net"],
            "payslips": [
                {
                    "employee": s["employee"],
                    "employee_name": s["employee_name"],
                    "base_salary": s["base_salary"],
                    "commission_earned": s["commission_earned"],
                    "bonuses": s["bonuses"],
                    "gross_pay": s["gross_pay"],
                    "paye_deduction": s["paye_deduction"],
                    "pension_deduction": s["pension_deduction"],
                    "other_deductions": s["other_deductions"],
                    "salary_deductions_m26": s["salary_deductions_m26"],
                    "net_pay": s["net_pay"],
                }
                for s in payslips
            ]
        })
        run.insert(ignore_permissions=True)
        frappe.db.commit()

        # Mark M26 salary deductions as processed
        _process_salary_deductions(month_start, month_end)

        # Update YTD for each employee
        for s in payslips:
            _update_ytd(s["employee"], s["gross_pay"])

        result["payroll_run"] = run.name

        # Loop 5: settle champion bonuses — only reached on a successful real run.
        from vitalvida.loop5.payroll_seam import settle_champion_bonuses
        for s in payslips:
            settle_champion_bonuses(s["employee"])
        frappe.db.commit()

    except Exception as e:
        frappe.log_error(
            f"M28: Payroll run failed for {month_start}: {str(e)}",
            "M28 Payroll Error"
        )
        result["error"] = str(e)

    return result


def _compute_payslip(emp: dict, month_start: str, month_end: str,
                     settings) -> dict:
    """Compute a single employee's payslip."""
    base_salary = float(emp.base_salary or 0)
    commission = 0.0
    # Loop 5: read-only preview of approved, unpaid, unvoided champion bonuses.
    from vitalvida.loop5.payroll_seam import preview_amount
    bonuses = preview_amount(emp["name"] if isinstance(emp, dict) else emp)

    # Telesales commission
    if emp.commission_eligible and emp.linked_closer and settings:
        commission = _compute_telesales_commission(
            emp.linked_closer, month_start, month_end, settings
        )

    # DA commission from M26 approved payouts
    if emp.commission_eligible and emp.linked_da:
        commission += _compute_da_commission(
            emp.linked_da, month_start, month_end
        )

    gross_pay = base_salary + commission + bonuses

    # PAYE on annual gross, divided by 12
    annual_gross = gross_pay * 12
    annual_paye = compute_paye(annual_gross)
    paye_deduction = round(annual_paye / 12, 2)

    # Pension: 8% of base salary (employee portion)
    pension_deduction = round(base_salary * 0.08, 2)

    # M26 salary deductions
    m26_deductions = _get_pending_salary_deductions(
        emp.name, month_start, month_end
    )

    other_deductions = 0.0
    net_pay = round(
        gross_pay - paye_deduction - pension_deduction
        - other_deductions - m26_deductions,
        2
    )
    net_pay = max(net_pay, 0)  # Never negative

    return {
        "employee": emp.name,
        "employee_name": emp.employee_name,
        "base_salary": base_salary,
        "commission_earned": round(commission, 2),
        "bonuses": bonuses,
        "gross_pay": round(gross_pay, 2),
        "paye_deduction": paye_deduction,
        "pension_deduction": pension_deduction,
        "other_deductions": other_deductions,
        "salary_deductions_m26": m26_deductions,
        "net_pay": net_pay,
    }


def _compute_telesales_commission(closer: str, month_start: str,
                                   month_end: str, settings) -> float:
    """Count delivered (Paid) orders and compute commission from M23 settings."""
    paid_count = frappe.db.count("VV Order", {
        "telesales_rep": closer,
        "order_status": "Paid",
        "paid_at": ["between", [month_start, month_end]]
    })

    commission_type = getattr(settings, "telesales_commission_type", "Per Order")

    if commission_type == "Per Order":
        rate = float(getattr(settings, "telesales_commission_amount", None) or 0)
        return paid_count * rate
    else:
        # Percentage of order value
        pct = float(getattr(settings, "telesales_commission_pct", None) or 0)
        total_value = frappe.db.sql("""
            SELECT COALESCE(SUM(total_payable), 0) as total
            FROM `tabVV Order`
            WHERE telesales_rep = %s AND order_status = 'Paid'
            AND paid_at BETWEEN %s AND %s
        """, (closer, month_start, month_end), as_dict=True)
        value = float(total_value[0].total) if total_value else 0.0
        return value * pct / 100


def _compute_da_commission(delivery_agent: str, month_start: str,
                           month_end: str) -> float:
    """Sum of approved (CEO Approved or Paid) payout records from M26."""
    result = frappe.db.sql("""
        SELECT COALESCE(SUM(total_payout_amount), 0) as total
        FROM `tabDA Payout Record`
        WHERE delivery_agent = %s
        AND status IN ('CEO Approved', 'Paid')
        AND modified BETWEEN %s AND %s
    """, (delivery_agent, month_start, month_end), as_dict=True)
    return float(result[0].total) if result else 0.0


def _get_pending_salary_deductions(employee: str, month_start: str,
                                    month_end: str) -> float:
    """Get total pending M26 salary deductions for this employee in this period."""
    result = frappe.db.sql("""
        SELECT COALESCE(SUM(amount), 0) as total
        FROM `tabSalary Deduction`
        WHERE employee = %s
        AND status = 'Pending'
        AND (deduction_date IS NULL OR deduction_date BETWEEN %s AND %s)
    """, (employee, month_start, month_end), as_dict=True)
    return float(result[0].total) if result else 0.0


def _process_salary_deductions(month_start: str, month_end: str) -> None:
    """Mark all pending deductions in this period as processed."""
    frappe.db.sql("""
        UPDATE `tabSalary Deduction`
        SET status = 'Processed', deduction_date = %s
        WHERE status = 'Pending'
        AND (deduction_date IS NULL OR deduction_date BETWEEN %s AND %s)
    """, (month_start, month_start, month_end))
    frappe.db.commit()


def _update_ytd(employee: str, gross_pay: float) -> None:
    """Update employee's year-to-date earnings."""
    try:
        current_ytd = float(
            frappe.db.get_value("VV Employee", employee, "total_earned_ytd") or 0
        )
        frappe.db.set_value("VV Employee", employee,
                            "total_earned_ytd", current_ytd + gross_pay)
    except Exception:
        pass


def compute_paye(annual_gross: float) -> float:
    """
    Compute Nigerian PAYE income tax using configurable Tax Band DocType.
    Falls back to standard Nigerian bands if none configured.
    """
    bands = frappe.get_all(
        "Tax Band",
        fields=["lower_limit", "upper_limit", "rate_percent"],
        order_by="lower_limit asc"
    )

    if not bands:
        # Default Nigerian PAYE bands (2024)
        bands = [
            {"lower_limit": 0, "upper_limit": 300000, "rate_percent": 7},
            {"lower_limit": 300000, "upper_limit": 600000, "rate_percent": 11},
            {"lower_limit": 600000, "upper_limit": 1100000, "rate_percent": 15},
            {"lower_limit": 1100000, "upper_limit": 1600000, "rate_percent": 19},
            {"lower_limit": 1600000, "upper_limit": 3200000, "rate_percent": 21},
            {"lower_limit": 3200000, "upper_limit": 0, "rate_percent": 24},
        ]

    total_tax = 0.0
    remaining = annual_gross

    for band in bands:
        lower = float(band["lower_limit"] or 0)
        upper = float(band["upper_limit"] or 0)
        rate = float(band["rate_percent"] or 0)

        if remaining <= 0:
            break

        if upper <= 0:
            # No upper limit — tax everything remaining
            taxable = remaining
        else:
            band_width = upper - lower
            taxable = min(remaining, band_width)

        total_tax += taxable * rate / 100
        remaining -= taxable

    return round(total_tax, 2)
