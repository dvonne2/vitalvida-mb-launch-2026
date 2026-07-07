"""
Loop 4 — Education binding (BIND + INFLUENCE; does NOT duplicate education_journey.py).

The existing vitalvida/education_journey.py owns the post-delivery education sequence
(Education Journey State + Education1..6 templates). Loop 4 does not rebuild it. Loop 4:
  1. OBSERVES education state and records progress on the Customer Timeline (memory).
  2. INFLUENCES the relationship as ENGAGEMENT only: completion is recorded as
     relationship memory and can drive follow-ups (e.g. the NBA engine's "Check
     Outcome" prompt). Completion does NOT grant Outcome=Achieved or Trust —
     outcome must be earned from direct evidence, never inferred from content
     consumption.

Reads Loop 1 education state; writes only Loop 4 relationship records. Does not
modify Loop 1.
"""
import frappe


def education_progress_for(customer_phone):
    """Read the customer's education journey state (by phone) from the existing engine."""
    if not customer_phone:
        return None
    rows = frappe.get_all("Education Journey State",
        filters={"customer_phone": customer_phone},
        fields=["name", "order", "current_step", "kill_switch", "killed_reason",
                "last_sent_at"], order_by="last_sent_at desc", limit=1)
    return rows[0] if rows else None


def sync_education(customer, customer_phone=None):
    """
    Record education progress on the timeline. Education completion is engagement,
    not transformation: it grants no Outcome and no Trust (see module docstring).
    """
    from vitalvida.customer_relationship.identity import normalize_phone
    phone = customer_phone or customer
    st = education_progress_for(normalize_phone(phone) or phone)
    if not st:
        return {"education": "none"}

    from vitalvida.customer_relationship.timeline import record_event
    record_event(customer, "Education Step",
                 summary=f"Education step {st.get('current_step')}"
                         + (" (completed)" if st.get("kill_switch") else ""),
                 ref_doctype="Education Journey State", ref_name=st["name"],
                 channel="WhatsApp", source="Loop 1")

    completed = bool(st.get("kill_switch")) and (st.get("killed_reason") == "Completed")
    if completed:
        # Education completion is ENGAGEMENT, not transformation. We never infer a
        # real-world business fact (the customer achieved what they bought the product
        # for) from an indirect signal (finishing the education sequence) when we can
        # wait for direct evidence. Completion grants NO Outcome and NO Trust; it is
        # recorded on the timeline above as relationship memory. The Relationship NBA
        # engine surfaces a "Check Outcome" follow-up once a delivered+paid order
        # exists, so outcome is EARNED from direct evidence (repeat delivered+paid,
        # positive review, or explicit measurement) rather than assumed.
        return {"education": "completed", "outcome_nudged": False}
    return {"education": "in_progress", "step": st.get("current_step")}
