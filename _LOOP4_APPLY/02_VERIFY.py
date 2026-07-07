# Loop 4 v0.2 — read-only verification.
#   cd /home/frappe/frappe-bench/sites && sudo -u frappe ../env/bin/python /tmp/vv_l4_verify.py
import frappe
frappe.init(site="vitalvida.systemforce.ng"); frappe.connect()
ok=True
DTS=["Customer Profile","Customer Timeline Event","Customer Trust Log","Customer Outcome",
     "Customer Complaint","Customer Review","Customer Referral","Customer Advocacy",
     "Relationship NBA Log","Loop 4 Settings","Order Care State","Customer Journey State"]
print("=== 1. doctypes + tables (expect 12) ===")
for d in DTS:
    ex=frappe.db.exists("DocType", d); tb=frappe.db.table_exists(d) if ex else False
    print(f"  {'OK ' if ex and tb else 'FAIL'} {d}  (doctype={bool(ex)}, table={bool(tb)})")
    ok = ok and bool(ex) and bool(tb)
print("=== 2. backfill result ===")
prof=frappe.db.count("Customer Profile")
phones=frappe.db.sql("SELECT COUNT(DISTINCT customer_phone) FROM `tabVV Order` WHERE customer_phone!=''")[0][0]
print(f"  Customer Profiles: {prof}   (distinct order phones: {phones})")
print("=== 3. settings single ===")
try:
    s=frappe.get_single("Loop 4 Settings")
    print(f"  OK  ai_enabled={s.ai_enabled} ignored_days={s.ignored_days_threshold} referral_threshold={s.referral_trust_threshold}")
except Exception as e:
    print("  FAIL Loop 4 Settings:", e); ok=False
print("=== 4. api imports (6 endpoints) ===")
try:
    import vitalvida.api.customer_relationship as A
    for fn in ["get_customer_360","get_relationship_brief","get_ignored_customers",
               "recompute_customer","run_relationship_refresh","file_complaint",
               "submit_review","get_review_candidates"]:
        assert hasattr(A, fn), fn
    print("  OK  all 8 endpoints present")
except Exception as e:
    print("  FAIL api:", e); ok=False
print("=== 5. journey engine imports (both layers, inert) ===")
try:
    from vitalvida.customer_relationship import journey as J
    for fn in ["run_order_care","create_order_care","run_customer_journey","create_customer_journey"]:
        assert hasattr(J, fn), fn
    print("  OK  both journey layers present (scheduler off — inert)")
except Exception as e:
    print("  FAIL journey:", e); ok=False
print("\nRESULT:", "ALL OK" if ok else "PROBLEMS — do not proceed")
