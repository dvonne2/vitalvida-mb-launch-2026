"""
Loop 5 settings access.

Ladders and thresholds live on the EXISTING `VV Commission Settings` singleton
(extended by the Loop 5 install patch), never hard-coded. This module is the
single read point so no engine hard-codes a naira value.
"""

import frappe
import json

# Ladder keys are stored as JSON strings on VV Commission Settings.
_JSON_KEYS = {"dpsr_ladder", "revival_ladder", "cart_ladder"}

# Defaults used ONLY as a safety net if the settings patch has not run yet.
# The install patch (patches/v5_0/seed_loop5_settings.py) writes these onto
# VV Commission Settings so the real source of truth is the DB, not this file.
_DEFAULTS = {
    "upsell_commission_amount": 1000.0,
    "upsell_min_incremental_value": 5000.0,      # naira floor; see Loop 5 Settings
    "upsell_min_incremental_mode": "Amount",     # "Amount" or "Percent"
    "upsell_min_incremental_percent": 15.0,
    "revival_dormancy_days": 30,
    # DPSR ladder: list of (min_dsr_strict, bonus). Highest matching tier wins.
    "dpsr_ladder": [(60, 5000), (70, 15000), (80, 50000), (90, 70000), (100, 100000)],
    # Revival ladder: (customers_reactivated, per_customer, total_bonus)
    "revival_ladder": [(5, 5000, 25000), (10, 6000, 60000), (20, 6500, 130000),
                       (30, 6667, 200000), (40, 6875, 275000), (50, 7000, 350000),
                       (75, 7000, 525000), (100, 7000, 700000)],
    # Cart ladder: (orders_recovered, per_order, total_bonus)
    "cart_ladder": [(5, 2000, 10000), (10, 2500, 25000), (20, 3000, 60000),
                    (30, 3500, 105000), (40, 4000, 160000), (50, 4500, 225000),
                    (75, 5000, 375000), (100, 6000, 600000)],
}


def _settings():
    from vitalvida.vitalvida.doctype.vv_commission_settings.vv_commission_settings import (
        get_commission_settings,
    )
    return get_commission_settings()


def get(key):
    """Read a Loop 5 tunable. Prefers the VV Commission Settings value; falls
    back to _DEFAULTS only when the field is absent (pre-patch safety)."""
    try:
        s = _settings()
        val = getattr(s, key, None)
        if val not in (None, ""):
            if key in _JSON_KEYS and isinstance(val, str):
                try:
                    return json.loads(val)
                except Exception:
                    return _DEFAULTS.get(key)
            return val
    except Exception:
        # Settings not installed/seeded yet — fall back to defaults.
        pass
    return _DEFAULTS.get(key)


def upsell_commission_amount() -> float:
    return float(get("upsell_commission_amount") or 0)


def revival_dormancy_days() -> int:
    return int(get("revival_dormancy_days") or 30)


def qualifies_min_incremental(original_value: float, new_value: float) -> bool:
    """Anti-gaming gate (business decision #5): an upsell only qualifies for
    commission if it adds meaningful value. Configurable amount or percent."""
    delta = float(new_value or 0) - float(original_value or 0)
    if delta <= 0:
        return False
    mode = (get("upsell_min_incremental_mode") or "Amount")
    if mode == "Percent":
        base = float(original_value or 0)
        if base <= 0:
            return delta > 0
        pct = (delta / base) * 100.0
        return pct >= float(get("upsell_min_incremental_percent") or 0)
    return delta >= float(get("upsell_min_incremental_value") or 0)


def dpsr_bonus_for(dsr_strict: float) -> float:
    """Highest DPSR ladder tier whose threshold <= dsr_strict."""
    ladder = get("dpsr_ladder") or []
    earned = 0.0
    for min_dsr, bonus in sorted(ladder, key=lambda t: t[0]):
        if float(dsr_strict) >= float(min_dsr):
            earned = float(bonus)
    return earned


def _ladder_total(ladder, count):
    """Highest tier total whose count threshold <= count (cumulative model)."""
    total = 0.0
    for tier in sorted(ladder or [], key=lambda t: t[0]):
        threshold = tier[0]
        tier_total = tier[2]
        if int(count) >= int(threshold):
            total = float(tier_total)
    return total


def revival_bonus_for(reactivated_count: int) -> float:
    return _ladder_total(get("revival_ladder"), reactivated_count)


def cart_bonus_for(recovered_count: int) -> float:
    return _ladder_total(get("cart_ladder"), recovered_count)
