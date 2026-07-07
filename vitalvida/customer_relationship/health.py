"""
Loop 4 — Customer Health Engine (churn risk), distinct from Trust.

Future-proof design (per architectural review): health is NOT a fixed 45-day rule.
It asks "is this customer behaving differently from what we'd expect for the products
they buy?" by comparing days-since-last-purchase against an EXPECTED cadence, where the
expected cadence is the best available of:
  1. the customer's OWN observed buying interval (median gap between their orders), else
  2. a per-category default cadence (Loop 4 Settings, keyed by primary_product_category), else
  3. the global default reorder cycle (Loop 4 Settings.reorder_cycle_days).

Bands are expressed as MULTIPLES of expected cadence, so a 30-day-cycle product and a
180-day-cycle product are judged on their own terms. Bootstrap: below min_orders_for_health
delivered+paid orders -> Insufficient Data (never guess).
"""
import frappe
from frappe.utils import get_datetime, date_diff, nowdate, cint

# per-category default cadence (days) when the customer has too few orders to self-estimate
CATEGORY_CADENCE_DEFAULT = {
    "Haircare": 45, "Supplements": 30, "Jewellery": 180, "Watches": 365, "General": 60,
}


def _settings_val(field, default):
    try:
        v = frappe.db.get_single_value("Loop 4 Settings", field)
        return cint(v) if v else default
    except Exception:
        return default


def _expected_cadence(customer, dp_orders):
    """Best available expected reorder interval in days for this customer."""
    # 1. customer's own median inter-order gap (needs >=2 dated orders)
    dates = sorted(get_datetime(o.get("paid_at") or o.get("delivered_at") or o.get("creation"))
                   for o in dp_orders)
    if len(dates) >= 2:
        gaps = [date_diff(str(dates[i])[:10], str(dates[i-1])[:10]) for i in range(1, len(dates))]
        gaps = [g for g in gaps if g and g > 0]
        if gaps:
            gaps.sort()
            return gaps[len(gaps)//2]  # median observed cadence
    # 2. per-category default
    cat = frappe.db.get_value("Customer Profile", customer, "primary_product_category")
    if cat and cat in CATEGORY_CADENCE_DEFAULT:
        return CATEGORY_CADENCE_DEFAULT[cat]
    # 3. global default
    return _settings_val("reorder_cycle_days", 45)


def compute_health(customer):
    """Return {score, band, reason, expected_cadence_days, days_since}. Cadence-relative."""
    from vitalvida.customer_relationship.identity import orders_for_customer, is_delivered_and_paid
    min_orders = _settings_val("min_orders_for_health", 2)
    orders = orders_for_customer(customer)
    dp = [o for o in orders if is_delivered_and_paid(o)]
    if not dp or len(dp) < min_orders:
        return {"score": None, "band": "Insufficient Data", "expected_cadence_days": None,
                "days_since": None,
                "reason": f"{len(dp)} delivered+paid order(s); need {min_orders} to score health."}
    cadence = _expected_cadence(customer, dp)
    last = max(get_datetime(o.get("paid_at") or o.get("delivered_at") or o.get("creation")) for o in dp)
    days_since = date_diff(nowdate(), str(last)[:10])
    ratio = days_since / cadence if cadence else 999
    # bands as multiples of the customer's EXPECTED cadence (future-proof across products)
    if ratio <= 1.0:
        band, score = "Healthy", 90
    elif ratio <= 1.5:
        band, score = "Watch", 65
    elif ratio <= 3.0:
        band, score = "At Risk", 35
    else:
        band, score = "Critical", 15
    return {"score": score, "band": band, "expected_cadence_days": cadence,
            "days_since": days_since,
            "reason": f"{days_since}d since last success vs ~{cadence}d expected "
                      f"({ratio:.1f}x cadence); {len(dp)} delivered+paid."}


def refresh_health(customer):
    h = compute_health(customer)
    prof = frappe.get_doc("Customer Profile", customer)
    prof.health_score = h["score"] or 0
    prof.health_band = h["band"]
    prof.save(ignore_permissions=True)
    return h
