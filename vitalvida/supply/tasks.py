"""
VitalVida Loop 3 — scheduler entrypoints (thin wrappers).

Each is safe to run daily and idempotent (the underlying functions upsert by
date-keyed names / idempotency keys, so a second run on the same day updates
rather than duplicates). Wrapped in try/except so one failing job never blocks
the rest of the scheduler; failures are logged.
"""
import frappe


def _safe(fn, label):
    try:
        res = fn()
        frappe.logger("loop3").info(f"{label}: {res}")
        return res
    except Exception as e:
        frappe.log_error(f"{label} failed: {e}", "Loop 3 Scheduler")
        return None


def daily_supply_planner():
    from vitalvida.supply.planner import run_supply_planner
    return _safe(run_supply_planner, "daily_supply_planner")


def daily_lofr():
    from vitalvida.supply.lofr import build_lofr_report
    return _safe(build_lofr_report, "daily_lofr")


def daily_replenishment_refresh():
    from vitalvida.supply.decision_engine import refresh_replenishment_plans
    return _safe(refresh_replenishment_plans, "daily_replenishment_refresh")


def daily_market_coverage():
    from vitalvida.supply.planner import scan_market_coverage
    return _safe(scan_market_coverage, "daily_market_coverage")


def daily_supply_exceptions():
    from vitalvida.supply.planner import scan_supply_exceptions
    return _safe(scan_supply_exceptions, "daily_supply_exceptions")
