# Loop 4 v0.2 — dry-run (writes ONLY Loop 4 records; safe, idempotent).
#   cd /home/frappe/frappe-bench/sites && sudo -u frappe ../env/bin/python /tmp/vv_l4_dryrun.py
import frappe
frappe.init(site="vitalvida.systemforce.ng"); frappe.connect()
from vitalvida.customer_relationship.runner import run_relationship_refresh
print("=== recompute all profiles (idempotent; seeds customer journeys, sends nothing) ===")
print(" ", run_relationship_refresh(limit=0))
name = frappe.db.get_value("Customer Profile", {}, "name")
if name:
    from vitalvida.api.customer_relationship import get_customer_360
    v = get_customer_360(name); p = v.get("profile", {})
    print("=== sample Customer 360 ===")
    # Trust is EARNED: present the BAND (Provisional until delivered+paid), not a raw number.
    tb = p.get("trust_band") or "Provisional"
    ts = p.get("trust_score")
    trust_display = tb if tb == "Provisional" else f"{tb} ({ts})"
    print("  phone:", p.get("phone"), "| stage:", p.get("lifecycle_stage"),
          "| trust:", trust_display, "(internal score:", ts, ")",
          "| health:", p.get("health_band"), "| outcome:", p.get("outcome_status"),
          "| success:", p.get("customer_success_state"), "| NBA:", p.get("next_best_action"))
    print("  orders:", len(v.get("orders",[])), "| timeline:", len(v.get("timeline",[])),
          "| referral_eligible:", p.get("referral_eligible"))
    print("=== ignored-customers scan (Law 6) ===")
    from vitalvida.api.customer_relationship import get_ignored_customers
    ig = get_ignored_customers()
    print("  threshold:", ig.get("threshold_days"), "days | flagged:", ig.get("count"))
    print("=== journey seeding check ===")
    print("  Customer Journey State rows:", frappe.db.count("Customer Journey State"))
else:
    print("  (no profiles — nothing to sample)")
    print("\n=== DISTRIBUTION across all profiles (sanity vs prediction) ===")
    from collections import Counter
    allp = frappe.get_all("Customer Profile",
        fields=["trust_band","health_band","outcome_status","customer_success_state","lifecycle_stage","referral_eligible"])
    for field in ["trust_band","health_band","outcome_status","customer_success_state","lifecycle_stage"]:
        dist = Counter((x.get(field) or "(none)") for x in allp)
        print(f"  {field}: {dict(dist)}")
    elig = sum(1 for x in allp if x.get("referral_eligible"))
    print(f"  referral_eligible=Yes: {elig} (prediction: 0)")
    print("\nPREDICTION TO VERIFY: all Provisional trust, all Insufficient Data health,")
    print("  all Unknown outcome, 0 referral-eligible, stage Prospect/First-Time. Anything")
    print("  more 'sophisticated' on this 0-delivered-paid dataset is a RED FLAG, not success.")
    print("\nDRY-RUN COMPLETE (no Loop 1-3 data modified; no messages sent — scheduler off).")
