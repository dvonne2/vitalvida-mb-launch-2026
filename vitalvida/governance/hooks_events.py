"""Optional doc_events secondary immutability guard (Option 3, part 2).

The doctype controllers already enforce immutability in validate()/on_trash()
(primary, active on install). To add the belt-and-suspenders doc_events layer,
merge the following into the app hooks.py — it calls the SAME shared utility, so
there is no duplicated logic:

    from vitalvida.governance.immutable import guard_no_delete
    _IMMUTABLE_EVENTS = [
        "Control Execution Event", "Control Resolution Event",
        "Schema Validation Event", "COA Drift Event",
        "Consumer Activation Request", "Consumer Activation Approval Event",
        "Consumer Activation Event", "Consumer Activation Reversal Event",
    ]
    doc_events = {dt: {"on_trash": "vitalvida.governance.immutable.guard_no_delete"}
                  for dt in _IMMUTABLE_EVENTS}

Field-level freeze is already enforced by each controller's validate().
"""
