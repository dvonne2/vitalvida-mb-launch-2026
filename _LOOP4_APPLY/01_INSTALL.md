# VitalVida Loop 4 — Customer Relationship Engine v0.2 — INSTALL

Relationship engine ONLY (no sales/earnings/champions/upsell — reserved for Loop 5).
Additive: 12 new doctypes + module package + 1 API file + 1 patch. Touches NO existing
Loop 1–3 code and adds NO field to VV Order. All messaging reuses notifications.py.

## 0. Freeze a restore point FIRST
- DigitalOcean snapshot: `VV-Loop4-pre-install`
- Confirm Loop 3 is committed/tagged (loop3-supply-v1.0).

## 1. Download (gdown)
```bash
cd /home/frappe
gdown "<FILE_ID>" -O /home/frappe/vitalvida-loop4-release.tar.gz
sha256sum /home/frappe/vitalvida-loop4-release.tar.gz   # MUST match published checksum
```

## 2. Extract
```bash
chmod 644 /home/frappe/vitalvida-loop4-release.tar.gz
cd /home/frappe/frappe-bench/apps/vitalvida
sudo -u frappe tar -xzf /home/frappe/vitalvida-loop4-release.tar.gz --strip-components=0
sudo chown -R frappe:frappe vitalvida/customer_relationship vitalvida/api/customer_relationship.py \
  vitalvida/patches/loop4_backfill_customer_profiles.py \
  vitalvida/doctype/customer_profile vitalvida/doctype/customer_timeline_event \
  vitalvida/doctype/customer_trust_log vitalvida/doctype/customer_outcome \
  vitalvida/doctype/customer_complaint vitalvida/doctype/customer_review \
  vitalvida/doctype/customer_referral vitalvida/doctype/customer_advocacy \
  vitalvida/doctype/relationship_nba_log vitalvida/doctype/loop4_settings \
  vitalvida/doctype/order_care_state vitalvida/doctype/customer_journey_state
```

## 3. Parse-check BEFORE migrate (no cache writes)
```bash
cd /home/frappe/frappe-bench/apps/vitalvida
for f in $(find vitalvida/customer_relationship vitalvida/api/customer_relationship.py \
  vitalvida/patches/loop4_backfill_customer_profiles.py vitalvida/doctype/customer_profile \
  vitalvida/doctype/customer_timeline_event vitalvida/doctype/customer_trust_log \
  vitalvida/doctype/customer_outcome vitalvida/doctype/customer_complaint \
  vitalvida/doctype/customer_review vitalvida/doctype/customer_referral \
  vitalvida/doctype/customer_advocacy vitalvida/doctype/relationship_nba_log \
  vitalvida/doctype/loop4_settings vitalvida/doctype/order_care_state \
  vitalvida/doctype/customer_journey_state -name '*.py'); do
  python3 -c "import ast; ast.parse(open('$f').read())" && echo "OK $f" || echo "FAIL $f"
done
```
All must read `OK`. Any `FAIL` → STOP and paste it.

## 4. Register the backfill patch
Append under `[post_model_sync]` in `vitalvida/patches.txt`:
```
vitalvida.patches.loop4_backfill_customer_profiles
```

## 5. Migrate (creates 12 tables, runs backfill)
```bash
cd /home/frappe/frappe-bench
sudo -u frappe bench --site vitalvida.systemforce.ng migrate
```
Expect clean completion + a `[loop4_backfill] created=...` log line.

## 6. Verify + dry-run
Copy `02_VERIFY.py` and `03_DRYRUN.py` to /tmp and run each:
```bash
cd /home/frappe/frappe-bench/sites && sudo -u frappe ../env/bin/python /tmp/vv_l4_verify.py
cd /home/frappe/frappe-bench/sites && sudo -u frappe ../env/bin/python /tmp/vv_l4_dryrun.py
```
Paste output for review.

## NOTES
- Scheduler NOT modified. Both journey runners (run_order_care, run_customer_journey)
  are present but inert — NOTHING sends automatically. The customer journey arc is only
  *seeded* during profile recompute; sends require enabling the runner later.
- AI dormant until Loop 4 Settings has ai_enabled + provider + key.
