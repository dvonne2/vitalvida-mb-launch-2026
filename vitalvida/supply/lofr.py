"""
VitalVida Loop 3 — Local Order Fulfilment Rate (LOFR).

LOFR = orders fulfilled immediately from local DA stock / total local orders × 100.
This is a MEASURE, computed from VV Order history. It writes Local Order
Fulfilment Report rows; it never mutates stock or orders.

Interpretation (defensible v1 PROXY — see limitation below):
- "Total local orders" = VV Orders assigned to a DA in the window (order reached
  at least 'Assigned').
- "Fulfilled from local stock" = orders that reached 'Paid' (delivered-and-paid),
  i.e. the local DA actually fulfilled them. (Matches dsr.py: Paid is the only
  qualifying fulfilment event.)
- "Delayed due to stock" = orders Rescheduled in the window.

LIMITATION (must be understood before trusting the number): this v1 measure uses
"Paid" as a proxy for "fulfilled immediately from the local DA's existing stock."
That can OVERSTATE true LOFR, because an order can end up Paid even if it was
*not* fulfilled from stock already positioned locally — e.g. it waited for an
emergency replenishment, or was served after a transfer. True LOFR requires
knowing the order was fulfillable from local stock AT THE MOMENT IT WAS PLACED.

V2 TRUTH SOURCE (recommended, not in this build): add a field on VV Order such as
`stock_ready_at_order_time` (or `fulfilled_from_local_stock`) set at order
creation by checking whether the assigned DA already held a complete bundle. LOFR
would then count those orders, giving a true positioning metric rather than a
delivery proxy. Until that exists, treat this LOFR as an interim indicator that
trends correctly but may read high.

VERIFY BEFORE APPLYING: "delayed due to stock" has no explicit stockout flag on
VV Order; we approximate with order_status == 'Rescheduled'. Refine once a
dedicated stockout/reschedule-reason field exists.
"""
import frappe
from frappe.utils import nowdate, add_days, flt


def calculate_lofr(start_date=None, end_date=None, da=None):
    """
    Compute LOFR for a window, optionally for one DA. Returns a dict; does not write.
    """
    end_date = end_date or nowdate()
    start_date = start_date or add_days(end_date, -1)

    base = {"delivery_agent": ["is", "set"], "assigned_at": ["between", [start_date, end_date]]}
    if da:
        base = {"delivery_agent": da, "assigned_at": ["between", [start_date, end_date]]}

    total = frappe.db.count("VV Order", base)

    paid_filters = dict(base); paid_filters["order_status"] = "Paid"
    fulfilled = frappe.db.count("VV Order", paid_filters)

    delayed_filters = dict(base); delayed_filters["order_status"] = "Rescheduled"
    delayed = frappe.db.count("VV Order", delayed_filters)

    lofr = (fulfilled / total * 100.0) if total else 100.0
    return {
        "start_date": start_date, "end_date": end_date, "delivery_agent": da,
        "total_orders": total, "fulfilled_from_local_stock": fulfilled,
        "delayed_due_to_stock": delayed, "lofr_percent": round(lofr, 2),
    }


def build_lofr_report(on_date=None):
    """
    Idempotent daily LOFR report per DA. One row per (date, da). Re-running updates.
    """
    on_date = on_date or nowdate()
    das = [d.name for d in frappe.get_all("Delivery Agent", filters={"active": 1},
                                          fields=["name"])]
    n = 0
    for da in das:
        m = calculate_lofr(on_date, on_date, da)
        da_doc = frappe.db.get_value("Delivery Agent", da, ["state"], as_dict=True) or {}
        name = f"LOFR-{on_date}-{(da_doc.get('state') or 'NA')}-{da}"
        row = {
            "doctype": "Local Order Fulfilment Report", "report_date": on_date,
            "state": da_doc.get("state") or "", "delivery_agent": da,
            "total_orders": m["total_orders"],
            "fulfilled_from_local_stock": m["fulfilled_from_local_stock"],
            "delayed_due_to_stock": m["delayed_due_to_stock"],
            "lofr_percent": m["lofr_percent"], "revenue_at_risk": 0,
        }
        if frappe.db.exists("Local Order Fulfilment Report", name):
            doc = frappe.get_doc("Local Order Fulfilment Report", name)
            doc.update({k: v for k, v in row.items() if k != "doctype"})
            doc.save(ignore_permissions=True)
        else:
            frappe.get_doc(row).insert(ignore_permissions=True)
        n += 1
    frappe.db.commit()
    return {"date": on_date, "reports": n}
