# Loop 4 v0.2 — ROLLBACK

Loop 4 is purely additive. Safest first:

## A. Restore snapshot (cleanest)
Restore `VV-Loop4-pre-install`. Done.

## B. Surgical removal
1. Remove from `vitalvida/patches.txt`: `vitalvida.patches.loop4_backfill_customer_profiles`
2. Delete the 12 doctypes (UI or drop tables): Customer Profile, Customer Timeline Event,
   Customer Trust Log, Customer Outcome, Customer Complaint, Customer Review,
   Customer Referral, Customer Advocacy, Relationship NBA Log, Loop 4 Settings,
   Order Care State, Customer Journey State.
3. Remove files:
   ```
   rm -rf vitalvida/customer_relationship
   rm -f  vitalvida/api/customer_relationship.py
   rm -f  vitalvida/patches/loop4_backfill_customer_profiles.py
   rm -rf vitalvida/doctype/customer_profile vitalvida/doctype/customer_timeline_event \
          vitalvida/doctype/customer_trust_log vitalvida/doctype/customer_outcome \
          vitalvida/doctype/customer_complaint vitalvida/doctype/customer_review \
          vitalvida/doctype/customer_referral vitalvida/doctype/customer_advocacy \
          vitalvida/doctype/relationship_nba_log vitalvida/doctype/loop4_settings \
          vitalvida/doctype/order_care_state vitalvida/doctype/customer_journey_state
   ```
4. `bench --site vitalvida.systemforce.ng migrate` then `bench build`.

No Loop 1–3 file was modified; VV Order is untouched (no field added). Removal cannot
affect supply, custody, orders, telesales, payroll or messaging.
