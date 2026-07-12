"""Seed the Event Ownership Register with the 14 governed event TYPES.

This seeds CONFIGURATION (one row per event type), NOT historical occurrences.
It deliberately does NOT scan existing Payment Reconciliation Log / Bonus
Approval Request / DA Payout Record rows and turn their mere existence into
"events" — a Draft payout is not "DA Fee Earned"; a Pending bonus is not
"Commission Earned". Any occurrence backfill is out of scope for Package 01 and
must be status-qualified per its own package.
"""
import frappe
from vitalvida.integration.idempotency import ensure_once

# (event_key, name, bucket, authoritative_doctype, source_key_field,
#  producer_module, erpnext_consequence, policy_ref, notes)
EVENTS = [
    ("E1_PAYMENT_CONFIRMED", "Payment Confirmed", "B", "Payment Reconciliation Log",
     "order", "reconciliation.py", "Payment Entry", "SET/FIN; CORE-002",
     "ERPNext owns the Payment Entry; the log owns the Moniepoint match reasoning."),
    ("E2_INVENTORY_FULFILLED", "Inventory Fulfilled", "B", "DA Stock Entry",
     "reference_order", "deduction.py", "Delivery Note -> SLE", "INV-005 (R78)",
     "Movement is ERPNext; the POD Delivered+Paid trigger rule is VitalVida."),
    ("E3_ORDER_CLOSED", "Order Closed", "B", "Order Status Log",
     "order", "reconciliation.py", "Sales Invoice / GL", "ORD-003 (R89)",
     "Closure = delivered+paid+fulfilled+reconciled; invoice alone doesn't prove it."),
    ("E4_REVENUE_RECOGNISED", "Revenue Recognised", "A", "Sales Invoice",
     "", "reconciliation.py", "Sales Invoice / GL Entry", "FIN-001/003 (R16/R53), REP-003 (R115)",
     "ERPNext models revenue completely; only the recognition trigger is custom. No custom record."),
    ("E5_DA_FEE_EARNED", "DA Fee Earned", "B", "DA Payout Record",
     "order", "api/da.py", "Purchase Invoice / Payable -> Payment Entry", "SET-004 (R2/R17), PAY",
     "Payable is ERPNext; 'earned because order X hit Delivered&Paid under rule vX' is VitalVida."),
    ("E6_SETTLEMENT_REQUESTED", "Settlement Requested", "C", "Fee Payment Request",
     "", "api/da.py", "", "SET-006 (R42)",
     "Pure workflow state; ERPNext has no 'asked to be paid' concept."),
    ("E7_SETTLEMENT_APPROVED", "Settlement Approved", "C", "DA Payout Record",
     "", "da_payout_record.py", "", "SET-007 (R43)",
     "Approval state (Finance/CEO) is workflow; the consequence comes at Paid."),
    ("E8_SETTLEMENT_PAID", "Settlement Paid", "A", "Payment Entry",
     "", "da_payout_record.py", "Payment Entry (submitted)", "SET-008 (R44)",
     "Funds leaving = Payment Entry, expressed completely. No custom record."),
    ("E9_COMMISSION_EARNED", "Employee Commission Earned", "B", "Bonus Approval Request",
     "source_event", "loop5/champions.py", "Additional Salary -> Salary Slip", "PAY-004 (R96), PAY-011 (R102)",
     "Additional Salary is a payroll input; the rule-versioned earning fact is VitalVida. "
     "PENDING HRMS: Additional Salary consequence not installed on this site yet."),
    ("E10_DA_STOCK_DISPATCHED", "DA Stock Dispatched", "B", "DA Stock Entry",
     "reference_order", "stock.py", "Stock Entry -> SLE/Bin", "INV-001 (R74)",
     "Qty+valuation is ERPNext; custody evidence (photo, consignment, approvals) is VitalVida."),
    ("E11_STOCK_COUNT_VERIFIED", "Stock Count Verified", "B", "Stock Count",
     "", "cycle_count.py", "Stock Reconciliation", "INV (prov.)",
     "Correction is ERPNext Stock Reconciliation; the DA photo/count verification is VitalVida."),
    ("E12_DELIVERY_COMPLETED", "Delivery Completed", "B", "Da Proof Demand",
     "", "proof_demand.py", "Delivery Note", "ORD (prov.)",
     "Delivery record is ERPNext; the POD photo-proof workflow is VitalVida."),
    ("E13_COURSE_COMPLETED", "Course/Assessment Completed", "A", "LMS Certificate",
     "", "academy.py", "LMS / HRMS completion", "ACA-001/003 (R15/R17)",
     "Frappe LMS owns completion; only a small practical-assessment extension is custom. "
     "PENDING LMS: LMS Certificate authority not installed on this site yet."),
    ("E14_DISCIPLINE_INCIDENT", "Discipline Incident", "B", "Da Strike Log",
     "", "consignment_strike.py", "HRMS Additional Salary / Appraisal", "PAY (Discipline HR pattern)",
     "HRMS owns payroll/appraisal consequence; the event-sourced incident is VitalVida."),
]


def execute():
    skipped = []
    for (key, name, bucket, auth_dt, srckey, producer, conseq, policy, notes) in EVENTS:
        # Self-protection: never register an event as an active claim on an
        # authority doctype that does not exist on THIS site (e.g. HRMS/LMS not
        # installed). Seed it inactive with a note instead of throwing mid-migrate.
        active = 1
        if auth_dt and not frappe.db.exists("DocType", auth_dt):
            active = 0
            notes = f"[INACTIVE: authority DocType '{auth_dt}' not installed] " + notes
            skipped.append((key, auth_dt))
        res = ensure_once(
            "Event Definition", {"event_key": key},
            {"event_key": key, "event_name": name, "bucket": bucket,
             "authoritative_doctype": auth_dt or None, "source_key_field": srckey or None,
             "producer_module": producer, "erpnext_consequence": conseq or None,
             "policy_ref": policy, "is_active": active, "notes": notes})
        # keep config in sync on re-run without duplicating the row
        if not res["created"]:
            d = frappe.get_doc("Event Definition", res["name"])
            d.update({"event_name": name, "bucket": bucket,
                      "authoritative_doctype": auth_dt or None,
                      "source_key_field": srckey or None, "producer_module": producer,
                      "erpnext_consequence": conseq or None, "policy_ref": policy,
                      "is_active": active, "notes": notes})
            d.save(ignore_permissions=True)
    if skipped:
        frappe.log_error("\n".join(f"{k}: authority {dt} missing -> seeded inactive"
                                    for k, dt in skipped),
                         "Package01 seed: inactive events (missing authority)")
    frappe.db.commit()
