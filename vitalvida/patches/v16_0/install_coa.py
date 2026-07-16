"""Package 16 — read-only CoA drift auditing. Applies nothing, ever.

ERPNext Account remains the sole Chart of Accounts authority. This package
creates no accounts and has no application engine.
"""
from vitalvida.patches._register import register_events

EVENT_DEFINITIONS = [
    dict(event_key="vv.coa.drift_audited", event_name="COA Drift Audited", bucket="B",
         authoritative_doctype="COA Drift Event", producer_module="vitalvida.coa",
         erpnext_consequence="None (read-only audit evidence)", policy_ref="COA-001"),
]


def execute():
    register_events(EVENT_DEFINITIONS)
