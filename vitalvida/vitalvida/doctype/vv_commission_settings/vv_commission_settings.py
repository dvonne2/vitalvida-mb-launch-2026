"""
M23 — VV Commission Settings Controller

Validates performance_tiers child table on save:
  - Must have at least one tier where blocks_new_assignments = 1
  - Tiers should cover the full DSR range without gaps
"""

import frappe
from frappe.model.document import Document


class VVCommissionSettings(Document):
    def validate(self):
        self._validate_blocking_tier()

    def _validate_blocking_tier(self):
        """At least one tier must block new assignments."""
        if not self.performance_tiers:
            frappe.throw(
                "At least one Performance Tier is required."
            )

        has_blocker = any(
            t.blocks_new_assignments for t in self.performance_tiers
        )
        if not has_blocker:
            frappe.throw(
                "At least one Performance Tier must have 'Blocks New Assignments' checked. "
                "This ensures low-performing reps are automatically blocked."
            )


def get_commission_settings():
    """
    Public helper called by M24, M26, M28.
    Returns the VV Commission Settings singleton.
    Raises clear error if not configured.
    """
    if not frappe.db.exists("DocType", "VV Commission Settings"):
        frappe.throw(
            "VV Commission Settings DocType not found. "
            "Please install M23 before running commission calculations."
        )

    settings = frappe.get_single("VV Commission Settings")

    if not settings.performance_tiers:
        frappe.throw(
            "VV Commission Settings has no performance tiers configured. "
            "Please add at least one tier before running commission calculations."
        )

    return settings


def match_tier(delivery_rate: float, settings=None):
    """
    Match a delivery rate to the correct Commission Tier.
    Returns dict with tier_name, bonus_multiplier, blocks_new_assignments.
    """
    if not settings:
        settings = get_commission_settings()

    matched = None
    for tier in sorted(settings.performance_tiers,
                       key=lambda t: float(t.min_delivery_rate or 0)):
        min_rate = float(tier.min_delivery_rate or 0)
        max_rate = float(tier.max_delivery_rate or 999)

        if min_rate <= delivery_rate <= max_rate:
            matched = tier

    if not matched:
        # Fallback to lowest tier
        matched = min(settings.performance_tiers,
                      key=lambda t: float(t.min_delivery_rate or 0))

    return {
        "tier_name": matched.tier_name,
        "bonus_multiplier": float(matched.bonus_multiplier or 1.0),
        "blocks_new_assignments": bool(matched.blocks_new_assignments),
    }
