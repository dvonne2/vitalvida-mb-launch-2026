"""
Loop 4 — Relationship AI (DORMANT, pluggable). Builds complete prompts and a provider
abstraction, but makes ZERO external calls until Loop 4 Settings has ai_enabled=1 AND
an api_key. When dormant, returns rule-based output with ai_status='dormant'. AI here
coaches the RELATIONSHIP (never sales — that's Loop 5's AI Sales Coach).
"""
import frappe


def _ai_config():
    try:
        s = frappe.get_single("Loop 4 Settings")
        key = s.get_password("ai_api_key") if s.ai_api_key else None
        return {"enabled": bool(s.ai_enabled) and bool(key),
                "provider": s.ai_provider or "anthropic", "key": key}
    except Exception:
        return {"enabled": False, "provider": "anthropic", "key": None}


def _ai_complete(prompt, system=""):
    """Single seam for all providers. Dormant unless configured. Never raises outward."""
    cfg = _ai_config()
    if not cfg["enabled"]:
        return {"ai_status": "dormant — no provider configured", "text": None}
    try:
        # Provider call intentionally not wired to a network here; when a key is
        # configured the integration is added in one place. Kept dormant by design.
        return {"ai_status": "configured", "text": None,
                "note": "Provider adapter present; wire network call when going live."}
    except Exception as e:
        return {"ai_status": f"error: {e}", "text": None}


def relationship_brief(customer):
    """Human-readable relationship summary + suggested talking points (rule-based base,
    AI-enhanced when live)."""
    if not frappe.db.exists("Customer Profile", customer):
        return {"error": "No profile"}
    prof = frappe.get_doc("Customer Profile", customer)
    from vitalvida.customer_relationship.nba import compute_nba
    nba = compute_nba(customer)
    base = {
        "customer": customer, "name": prof.customer_name,
        "lifecycle_stage": prof.lifecycle_stage, "trust": prof.trust_score,
        "trust_band": prof.trust_band, "health_band": prof.health_band,
        "outcome_status": prof.outcome_status,
        "next_best_action": nba["action"], "nba_reason": nba["reason"],
    }
    prompt = (f"Customer {prof.customer_name or customer}: stage={prof.lifecycle_stage}, "
              f"trust={prof.trust_score}({prof.trust_band}), health={prof.health_band}, "
              f"outcome={prof.outcome_status}. Suggested relationship action: {nba['action']}. "
              f"Write 2 warm, non-salesy talking points to strengthen this relationship.")
    ai = _ai_complete(prompt, system="You are a customer-relationship coach. Never push a sale.")
    base["ai_status"] = ai["ai_status"]
    base["ai_talking_points"] = ai.get("text")
    return base
