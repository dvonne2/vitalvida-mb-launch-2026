"""
M10 — Weighted Round-Robin Telesales Assignment Engine
Atomic DB lock prevents duplicate assignment under concurrent load.
Entry point: assign_telesales_closer(order_name, brand)

Assignment modes (set in Vitalvida Settings → telesales_assignment_mode):
  - Round Robin          : pure sequential, equal distribution
  - Weighted Round Robin : sequential + performance weighting (DEFAULT)
  - Performance Weighted : top performers get more leads via random weighted draw

Fixes applied (client review):
  Fix 1 — Per-pool pointer via frappe.cache() — pools no longer share one global pointer
  Fix 2 — All-at-cap returns [] and escalates — cap is now a hard limit, no silent overflow
  Fix 3 — round_robin_index increment removed from _do_assignment() — field no longer used
"""

import random
import frappe
from frappe.utils import now_datetime


# ── Per-pool cache key ─────────────────────────────────────
# Each pool (General, FHG, IR) has its own independent pointer
POINTER_KEY = "vitalvida:telesales_rr_pointer:{pool}"


# ═══════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════

def assign_telesales_closer(order_name, brand):
    """
    Called from vv_order.py on Pending transition.
    1. Determines pool from brand
    2. Gets eligible closers (respects hard pending cap)
    3. Selects closer based on assignment mode
    4. Atomically assigns with DB lock
    """
    try:
        pool = _get_pool(brand)
        closers = _get_eligible_closers(pool)

        # Fallback to General pool if brand pool is empty
        if not closers and pool != "General":
            frappe.log_error(
                message=f"No active closers in pool '{pool}' for order {order_name}. Falling back to General pool.",
                title="M10 Pool Fallback"
            )
            pool = "General"
            closers = _get_eligible_closers(pool)

        # Fix 2: if all closers are at cap, stop — do not overflow
        if not closers:
            frappe.log_error(
                message=f"M10: No eligible closers for order {order_name} in any pool. "
                f"All closers may be at pending cap or no active closers exist. "
                f"A supervisor should review and manually assign.",
                title="M10 No Eligible Closers"
            )
            return

        # Get assignment mode from Vitalvida Settings
        settings = frappe.get_single("VitalVida Settings")
        mode = getattr(settings, "telesales_assignment_mode", None) or "Weighted Round Robin"

        # Select closer based on mode
        if mode == "Round Robin":
            closer = _select_round_robin(closers)
        elif mode == "Weighted Round Robin":
            closer = _select_weighted_round_robin(closers, pool)
        elif mode == "Performance Weighted":
            closer = _select_performance_weighted(closers, settings)
        else:
            closer = _select_weighted_round_robin(closers, pool)

        if not closer:
            frappe.log_error(
                message=f"M10: Closer selection returned None for order {order_name}.",
                title="M10 Selection Error"
            )
            return

        _do_assignment(order_name, closer, mode, pool)

    except Exception as e:
        frappe.log_error(
            message=f"Telesales assignment failed for order {order_name}: {str(e)}",
            title="M10 Assignment Error"
        )


# ═══════════════════════════════════════════════════════════
# POOL + ELIGIBILITY
# ═══════════════════════════════════════════════════════════

def _get_pool(brand):
    """Map order brand to assignment pool."""
    if brand == "FHG":
        return "FHG"
    elif brand == "IR":
        return "IR"
    return "General"


def _get_eligible_closers(pool):
    """
    Fetch all active, unblocked closers in this pool
    who are below their pending cap.

    Fix 2: If all closers are at cap, return [] — do NOT fall back
    to least-loaded. The cap is a hard limit. Caller handles the empty list.
    """
    all_closers = frappe.get_all(
        "Telesales Closer",
        filters={
            "pool": pool,
            "is_active": 1,
            "is_blocked": 0,
        },
        fields=[
            "name", "closer_name", "phone", "user",
            "last_assigned_at", "weekly_delivery_rate",
            "max_pending_override",
        ],
        order_by="last_assigned_at asc",
    )

    if not all_closers:
        return []

    # Get default pending cap from settings
    try:
        settings = frappe.get_single("VitalVida Settings")
        default_cap = int(getattr(settings, "max_pending_per_closer", None) or 20)
    except Exception:
        default_cap = 20

    # Only return closers below their cap
    eligible = []
    for c in all_closers:
        cap = int(c.get("max_pending_override") or default_cap)
        pending = frappe.db.count("VV Order", {
            "telesales_rep": c["name"],
            "order_status": ["in", ["Pending", "Rescheduled"]],
        })
        if pending < cap:
            c["pending_count"] = pending
            c["cap"] = cap
            eligible.append(c)

    # Fix 2: All at cap → return [] and let caller escalate
    if not eligible:
        frappe.log_error(
            message=f"M10: All active closers in pool '{pool}' are at their pending cap. "
            f"No assignment made. A supervisor should review unassigned orders.",
            title="M10 All At Cap"
        )
        return []

    return eligible


# ═══════════════════════════════════════════════════════════
# SELECTION MODES
# ═══════════════════════════════════════════════════════════

def _select_round_robin(closers):
    """
    Pure Round Robin.
    Picks the closer with the oldest last_assigned_at.
    Already sorted by last_assigned_at ASC from _get_eligible_closers().
    Sequence: A → B → C → A → B → C (equal distribution)
    """
    return closers[0] if closers else None


def _select_weighted_round_robin(closers, pool):
    """
    Weighted Round Robin — what the client wants.

    Fix 1: Each pool has its own pointer stored in frappe.cache().
    FHG pointer and General pointer are completely independent.

    Builds a weighted interleaved sequence then advances the
    pool-specific pointer through it one step per order.

    Example — pool=FHG with A=80%, B=65%, C=40%:
      Weights:    A=5, B=4, C=2
      Sequence:   [A, B, C, A, B, A, B, A, B, A, A]
      Pointer:    advances per FHG order only — General never touches it

    Example — pool=General with X=70%, Y=50%:
      Weights:    X=4, Y=3
      Sequence:   [X, Y, X, Y, X, Y, X]
      Pointer:    advances per General order only — FHG never touches it
    """
    if not closers:
        return None

    # Build weighted interleaved sequence for this pool
    sequence = _build_weighted_sequence(closers)
    if not sequence:
        return _select_round_robin(closers)

    # Fix 1: Get pool-specific pointer from cache
    pointer = _get_pool_pointer(pool)
    seq_len = len(sequence)

    # Walk the sequence from pointer
    # Skip anyone no longer eligible (filtered out by _get_eligible_closers)
    eligible_names = {c["name"] for c in closers}
    chosen_name = None
    attempts = 0

    while attempts < seq_len:
        idx = pointer % seq_len
        candidate_name = sequence[idx]
        pointer += 1
        attempts += 1

        if candidate_name in eligible_names:
            chosen_name = candidate_name
            break

    # Fix 1: Save updated pool-specific pointer
    _save_pool_pointer(pool, pointer)

    if not chosen_name:
        # All sequence slots were ineligible — fall back to round robin
        return _select_round_robin(closers)

    return next((c for c in closers if c["name"] == chosen_name), closers[0])


def _select_performance_weighted(closers, settings):
    """
    Performance Weighted (random draw, not sequential).
    Top X% of performers get lead_share% of assignments.
    """
    if not closers:
        return None

    top_percent = float(getattr(settings, "performance_weight_top_percent", None) or 20.0)
    lead_share = float(getattr(settings, "performance_weight_lead_share", None) or 40.0)

    sorted_closers = sorted(
        closers,
        key=lambda c: float(c.get("weekly_delivery_rate") or 0),
        reverse=True
    )

    total = len(sorted_closers)
    top_count = max(1, round(total * top_percent / 100))
    top_group = sorted_closers[:top_count]
    bottom_group = sorted_closers[top_count:]

    roll = random.random()
    if roll < (lead_share / 100):
        pool_to_use = top_group if top_group else sorted_closers
    else:
        pool_to_use = bottom_group if bottom_group else sorted_closers

    return random.choice(pool_to_use)


# ═══════════════════════════════════════════════════════════
# WEIGHTED SEQUENCE BUILDER
# ═══════════════════════════════════════════════════════════

def _build_weighted_sequence(closers):
    """
    Builds an interleaved weighted sequence from closers.

    Weights from weekly_delivery_rate:
      >= 80% → weight 5
      >= 65% → weight 4
      >= 50% → weight 3
      >= 35% → weight 2
      <  35% → weight 1  (everyone gets at least 1 slot)

    Interleaved (not grouped):
      Grouped     = [A, A, A, A, A, B, B, B, B, C, C]  ← bad
      Interleaved = [A, B, C, A, B, A, B, A, B, A, A]  ← correct

    Multi-pass round-robin expansion:
      Each pass assigns one slot to each closer who still has weight remaining.
    """
    if not closers:
        return []

    weighted = []
    for c in closers:
        rate = float(c.get("weekly_delivery_rate") or 50)
        weight = _rate_to_weight(rate)
        weighted.append({"name": c["name"], "weight": weight})

    sequence = []
    remaining = {w["name"]: w["weight"] for w in weighted}

    while any(v > 0 for v in remaining.values()):
        for w in weighted:
            if remaining[w["name"]] > 0:
                sequence.append(w["name"])
                remaining[w["name"]] -= 1

    return sequence


def _rate_to_weight(rate):
    """Maps weekly_delivery_rate (0–100) to weight (1–5)."""
    if rate >= 80:
        return 5
    elif rate >= 65:
        return 4
    elif rate >= 50:
        return 3
    elif rate >= 35:
        return 2
    else:
        return 1


# ═══════════════════════════════════════════════════════════
# PER-POOL POINTER  (Fix 1)
# ═══════════════════════════════════════════════════════════

def _get_pool_pointer(pool):
    """
    Reads the sequence pointer for a specific pool from frappe.cache().
    Each pool has a completely independent pointer.
    """
    key = POINTER_KEY.format(pool=pool)
    val = frappe.cache().get_value(key)
    return int(val or 0)


def _save_pool_pointer(pool, pointer):
    """Saves the updated pointer for a specific pool to frappe.cache()."""
    key = POINTER_KEY.format(pool=pool)
    frappe.cache().set_value(key, pointer)


# ═══════════════════════════════════════════════════════════
# ATOMIC ASSIGNMENT
# ═══════════════════════════════════════════════════════════

def _do_assignment(order_name, closer, mode, pool):
    """
    Lock + assign (committed), then best-effort log + ToDo + notify.

    The assignment itself (telesales_rep + last_assigned_at) is committed
    first. The audit log, ToDo and notification are secondary artifacts, each
    run in its own guard so a failure in any of them can NEVER roll back the
    assignment.

    REGRESSION FIX: previously all steps shared one try/except with a blanket
    frappe.db.rollback(). When the Telesales Assignment Log rejected the
    "Weighted Round Robin" mode value (missing from its Select options), the
    rollback also undid the telesales_rep set_value — so every order in the
    default mode silently ended up unassigned.

    Fix 3: round_robin_index increment removed — field no longer used for selection.
    """
    closer_name = closer["name"]
    now = now_datetime()

    # ── Primary: the assignment itself (atomic, committed) ───────────────
    try:
        # Acquire row-level DB lock on this closer
        frappe.db.sql(
            "SELECT name FROM `tabTelesales Closer` WHERE name = %s FOR UPDATE",
            (closer_name,)
        )
        frappe.db.set_value("Telesales Closer", closer_name, {"last_assigned_at": now})
        frappe.db.set_value("VV Order", order_name, "telesales_rep", closer_name)
        # Commit releases the FOR UPDATE lock and persists the assignment
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(
            message=f"M10 atomic assignment failed: closer={closer_name} "
            f"order={order_name}: {str(e)}",
            title="M10 Atomic Lock Error"
        )
        frappe.db.rollback()
        return  # assignment did not persist — skip downstream artifacts

    # ── Secondary: audit log (best-effort, never rolls back assignment) ──
    try:
        frappe.get_doc({
            "doctype": "Telesales Assignment Log",
            "order": order_name,
            "closer": closer_name,
            "assigned_at": now,
            "assignment_mode": mode,
            "pool": pool,
        }).insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception as e:
        frappe.log_error(
            message=f"M10 assignment log failed (assignment kept): closer={closer_name} "
            f"order={order_name}: {str(e)}",
            title="M10 Assignment Log Error"
        )

    # ── Secondary: ToDo for the closer's user (best-effort) ──────────────
    closer_user = closer.get("user")
    if closer_user:
        try:
            frappe.get_doc({
                "doctype": "ToDo",
                "description": f"Call customer for Order {order_name}",
                "assigned_by": "Administrator",
                "owner": closer_user,
                "reference_type": "VV Order",
                "reference_name": order_name,
            }).insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception as e:
            frappe.log_error(
                message=f"M10 ToDo creation failed (assignment kept): closer={closer_name} "
                f"order={order_name}: {str(e)}",
                title="M10 ToDo Error"
            )

    # ── Secondary: WhatsApp notification (already guarded internally) ────
    _notify_closer(order_name, closer_name)


# ═══════════════════════════════════════════════════════════
# NOTIFICATION
# ═══════════════════════════════════════════════════════════

def _notify_closer(order_name, closer_name):
    """Fire TelesalesAssigned WhatsApp notification to the closer."""
    try:
        from vitalvida.notifications import send_notification
        order = frappe.get_doc("VV Order", order_name)
        order.telesales_rep = closer_name
        send_notification(
            order,
            event="TelesalesAssigned",
            recipient_type="Telesales",
            sender_channel="Transactional",
        )
    except Exception as e:
        frappe.log_error(
            message=f"M10 notification failed: closer={closer_name} "
            f"order={order_name}: {str(e)}",
            title="M10 Notification Error"
        )


# ═══════════════════════════════════════════════════════════
# WHITELISTED UTILITIES
# ═══════════════════════════════════════════════════════════

@frappe.whitelist()
def assign_unassigned_orders():
    """
    Sweeps all Pending VV Orders with no telesales_rep assigned.
    Run manually from console or via scheduled job.
    """
    unassigned = frappe.get_all(
        "VV Order",
        filters={"order_status": "Pending", "telesales_rep": ["is", "not set"]},
        fields=["name", "brand"],
        order_by="creation asc",
        limit=500
    )

    if not unassigned:
        return {"success": True, "assigned": 0, "message": "No unassigned orders"}

    assigned_count = 0
    failed = []

    for order in unassigned:
        try:
            assign_telesales_closer(order["name"], order.get("brand", ""))
            assigned_count += 1
        except Exception as e:
            failed.append({"order": order["name"], "error": str(e)})

    return {
        "success": True,
        "assigned": assigned_count,
        "failed": len(failed),
        "failed_orders": failed,
    }


@frappe.whitelist()
def get_assignment_preview():
    """
    Returns a preview of the current weighted sequence per pool.
    Shows exactly how orders will be distributed before going live.
    """
    try:
        settings = frappe.get_single("VitalVida Settings")
        mode = getattr(settings, "telesales_assignment_mode", None) or "Weighted Round Robin"
        default_cap = int(getattr(settings, "max_pending_per_closer", None) or 20)
    except Exception:
        mode = "Weighted Round Robin"
        default_cap = 20

    pools = ["General", "FHG", "IR"]
    result = {}

    for pool in pools:
        closers = frappe.get_all(
            "Telesales Closer",
            filters={"pool": pool, "is_active": 1, "is_blocked": 0},
            fields=[
                "name", "closer_name", "weekly_delivery_rate",
                "last_assigned_at", "max_pending_override"
            ],
        )

        if not closers:
            continue

        sequence = _build_weighted_sequence(closers)
        # Fix 1: show pool-specific pointer
        pointer = _get_pool_pointer(pool)
        summary = []

        for c in closers:
            pending = frappe.db.count("VV Order", {
                "telesales_rep": c["name"],
                "order_status": ["in", ["Pending", "Rescheduled"]],
            })
            cap = int(c.get("max_pending_override") or default_cap)
            rate = float(c.get("weekly_delivery_rate") or 50)
            weight = _rate_to_weight(rate)
            slots = sequence.count(c["name"]) if sequence else 0
            summary.append({
                "closer": c["closer_name"],
                "id": c["name"],
                "delivery_rate": f"{rate}%",
                "weight": weight,
                "slots_in_sequence": slots,
                "share_pct": f"{round(slots / len(sequence) * 100, 1)}%" if sequence else "0%",
                "pending_orders": pending,
                "cap": cap,
                "at_cap": pending >= cap,
            })

        result[pool] = {
            "mode": mode,
            "sequence": sequence,
            "sequence_length": len(sequence),
            "pool_pointer": pointer,
            "next_idx": pointer % len(sequence) if sequence else 0,
            "next_closer": sequence[pointer % len(sequence)] if sequence else None,
            "closers": summary,
        }

    return result


@frappe.whitelist()
def reset_pointer(pool=None):
    """
    Resets the round-robin pointer.
    Fix 1: accepts optional pool parameter to reset one pool independently.
    If no pool given, resets all three pools.
    """
    pools_to_reset = [pool] if pool else ["General", "FHG", "IR"]
    for p in pools_to_reset:
        _save_pool_pointer(p, 0)
    frappe.db.commit()
    return {
        "success": True,
        "message": f"Pointer reset for: {', '.join(pools_to_reset)}"
    }

