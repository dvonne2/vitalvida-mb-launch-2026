"""
VitalVida Test Data Cleanup Script

Run in three modes:
  AUDIT:   show what looks like test data (no changes)
  DRY-RUN: show what WOULD be deleted (no changes)
  EXECUTE: actually delete (with confirmation prompts)

Usage:
  bench --site vitalvida.systemforce.ng execute \
    vitalvida.scripts.cleanup_test_data.audit
  bench --site vitalvida.systemforce.ng execute \
    vitalvida.scripts.cleanup_test_data.dry_run
  bench --site vitalvida.systemforce.ng execute \
    vitalvida.scripts.cleanup_test_data.execute
"""

import frappe
from frappe.utils import now_datetime


# ───────────────────────────────────────────────────────────────────
# Detection rules — what counts as "test data"
# ───────────────────────────────────────────────────────────────────
#
# These are heuristics. Review the output carefully before executing.
# False positives are possible — a real customer named "Test Customer"
# would be flagged. Always check the audit output before deleting.
#

TEST_PATTERNS = {
    # Emails containing these strings are test accounts
    "email_substrings": [
        "test@",
        "@test.",
        "+test",
        "olaniyisulaimon221",  # Previous developer's test email — leave in pattern list
        "demo@",
        "sample@",
        "@example.com",
        "@vitalvida.local",
        "@test.com",
    ],

    # Full names containing these are test records
    "name_substrings": [
        "Test ",
        "TEST ",
        "test ",
        "Demo ",
        "Sample ",
        "ACCEPT TEST",
        "Acceptance Test",
        "Developer Test",
        "QA Test",
    ],

    # Phone numbers that look fake
    "phone_patterns": [
        "08099999999",  # The classic placeholder
        "08000000000",
        "08111111111",
        "08123456789",
        "07000000000",
    ],

    # aff_id / utm_ref patterns
    "aff_id_patterns": [
        "MB-TEST",
        "MB-DEMO",
        "MB-XXX",
        "MB-0000",
    ],
}


# ───────────────────────────────────────────────────────────────────
# Helper — find all test-looking records of a doctype
# ───────────────────────────────────────────────────────────────────

def _find_test_records(doctype, fields_to_check):
    """
    Find records that match test patterns.

    Args:
        doctype: e.g., "VV Media Buyer"
        fields_to_check: dict of {field_name: pattern_list_key}
                         e.g., {"email": "email_substrings", "full_name": "name_substrings"}

    Returns:
        list of matching record names
    """
    found = set()

    for field, pattern_key in fields_to_check.items():
        patterns = TEST_PATTERNS.get(pattern_key, [])
        for pattern in patterns:
            records = frappe.get_all(
                doctype,
                filters={field: ["like", f"%{pattern}%"]},
                pluck="name"
            )
            found.update(records)

    return list(found)


# ───────────────────────────────────────────────────────────────────
# Audit functions — show what exists, no changes
# ───────────────────────────────────────────────────────────────────

def audit():
    """
    AUDIT MODE — Lists everything that looks like test data.
    Makes no changes. Safe to run.
    """
    print("\n" + "=" * 70)
    print("VITALVIDA TEST DATA AUDIT")
    print("=" * 70)
    print(f"Site: {frappe.local.site}")
    print(f"Time: {now_datetime()}")
    print()

    findings = {}

    # 1. VV Media Buyer test affiliates
    print("1. VV Media Buyer — test affiliates")
    print("-" * 70)
    mb_test = _find_test_records("VV Media Buyer", {
        "email": "email_substrings",
        "full_name": "name_substrings",
        "phone": "phone_patterns",
        "utm_ref": "aff_id_patterns",
    })
    findings["VV Media Buyer"] = mb_test

    if mb_test:
        for name in mb_test:
            mb = frappe.db.get_value("VV Media Buyer", name,
                ["full_name", "email", "phone", "utm_ref", "status", "is_active",
                 "total_lifetime_orders", "total_lifetime_earned"],
                as_dict=True)
            print(f"  • {name}")
            print(f"      Name: {mb.full_name}")
            print(f"      Email: {mb.email}")
            print(f"      Phone: {mb.phone}")
            print(f"      utm_ref: {mb.utm_ref}")
            print(f"      Status: {mb.status} (active={mb.is_active})")
            print(f"      Lifetime orders: {mb.total_lifetime_orders}, earned: ₦{mb.total_lifetime_earned}")
            print()
    else:
        print("  ✓ No test media buyers found")
        print()

    # 2. VV Orders attributed to test affiliates
    print("2. VV Order — orders attributed to test media buyers")
    print("-" * 70)
    test_orders = []
    if mb_test:
        test_orders = frappe.get_all("VV Order",
            filters={"media_buyer": ["in", mb_test]},
            fields=["name", "customer_name", "package_name", "order_status", "media_buyer", "creation"]
        )
    findings["VV Order (attributed to test affiliates)"] = [o.name for o in test_orders]

    if test_orders:
        print(f"  Found {len(test_orders)} orders attributed to test media buyers:")
        for o in test_orders[:20]:  # Show first 20
            print(f"  • {o.name} — {o.customer_name} ({o.package_name}) [{o.order_status}] → {o.media_buyer}")
        if len(test_orders) > 20:
            print(f"  ... and {len(test_orders) - 20} more")
        print()
    else:
        print("  ✓ No orders attributed to test media buyers")
        print()

    # 3. VV Orders with test customer info
    print("3. VV Order — orders with test customer names/emails/phones")
    print("-" * 70)
    direct_test_orders = _find_test_records("VV Order", {
        "customer_name": "name_substrings",
        "customer_email": "email_substrings",
        "customer_phone": "phone_patterns",
    })
    # Exclude overlap with already-found orders
    direct_test_orders = [n for n in direct_test_orders if n not in [o.name for o in test_orders]]
    findings["VV Order (test customers)"] = direct_test_orders

    if direct_test_orders:
        print(f"  Found {len(direct_test_orders)} orders with test customer info:")
        for name in direct_test_orders[:20]:
            o = frappe.db.get_value("VV Order", name,
                ["customer_name", "package_name", "order_status", "creation"], as_dict=True)
            print(f"  • {name} — {o.customer_name} ({o.package_name}) [{o.order_status}]")
        if len(direct_test_orders) > 20:
            print(f"  ... and {len(direct_test_orders) - 20} more")
        print()
    else:
        print("  ✓ No orders with test customer info")
        print()

    # 4. Frappe Users that look like test accounts
    print("4. User — Frappe users that look like test accounts")
    print("-" * 70)
    test_users = _find_test_records("User", {
        "email": "email_substrings",
        "full_name": "name_substrings",
    })
    # Exclude system users (Administrator, Guest)
    test_users = [u for u in test_users if u not in ("Administrator", "Guest")]
    findings["User"] = test_users

    if test_users:
        for email in test_users:
            user = frappe.db.get_value("User", email,
                ["full_name", "enabled", "user_type", "last_login"], as_dict=True)
            roles = frappe.get_roles(email)
            elevated = any(r in roles for r in ["System Manager", "Administrator"])
            warning = " ⚠️ ELEVATED PERMISSIONS" if elevated else ""
            print(f"  • {email}{warning}")
            print(f"      Full name: {user.full_name}")
            print(f"      Enabled: {user.enabled}, Type: {user.user_type}")
            print(f"      Last login: {user.last_login}")
            print(f"      Roles: {', '.join(roles)}")
            print()
    else:
        print("  ✓ No test users found")
        print()

    # 5. VV Order Nudge Log entries from test affiliates
    print("5. VV Order Nudge Log — nudges from test affiliates")
    print("-" * 70)
    test_nudges = []
    if mb_test:
        test_nudges = frappe.get_all("VV Order Nudge Log",
            filters={"media_buyer": ["in", mb_test]},
            pluck="name"
        )
    findings["VV Order Nudge Log"] = test_nudges
    print(f"  Found {len(test_nudges)} nudge log entries from test affiliates")
    print()

    # ── Summary ──
    print("=" * 70)
    print("AUDIT SUMMARY")
    print("=" * 70)
    total = 0
    for doctype, records in findings.items():
        count = len(records)
        total += count
        print(f"  {doctype}: {count} records")
    print(f"  TOTAL: {total} records flagged as test data")
    print()
    print("Next step: review the lists above carefully.")
    print("If everything looks correct, run dry_run() to preview deletion.")
    print()

    return findings


# ───────────────────────────────────────────────────────────────────
# Dry-run — show what WOULD be deleted, no changes
# ───────────────────────────────────────────────────────────────────

def dry_run():
    """
    DRY-RUN MODE — Show exactly what WOULD be deleted.
    Makes no changes. Safe to run.
    """
    print("\n" + "=" * 70)
    print("VITALVIDA TEST DATA CLEANUP — DRY RUN")
    print("=" * 70)
    print("This shows what WOULD be deleted. NO CHANGES MADE.")
    print()

    findings = audit()

    print("\n" + "=" * 70)
    print("DELETION PLAN")
    print("=" * 70)
    print()
    print("If you execute(), the following will be deleted IN THIS ORDER:")
    print("(Order matters — child records before parents)")
    print()

    deletion_order = [
        ("VV Order Nudge Log", findings.get("VV Order Nudge Log", [])),
        ("VV Order (attributed to test affiliates)", findings.get("VV Order (attributed to test affiliates)", [])),
        ("VV Order (test customers)", findings.get("VV Order (test customers)", [])),
        ("VV Media Buyer", findings.get("VV Media Buyer", [])),
        ("User", findings.get("User", [])),
    ]

    for label, records in deletion_order:
        print(f"  → {label}: {len(records)} records")
        for name in records[:5]:
            print(f"      - {name}")
        if len(records) > 5:
            print(f"      ... and {len(records) - 5} more")
        print()

    print("=" * 70)
    print("To actually delete, run: execute()")
    print("=" * 70)


# ───────────────────────────────────────────────────────────────────
# Execute — actually delete (with confirmation)
# ───────────────────────────────────────────────────────────────────

def execute(confirm=False):
    """
    EXECUTE MODE — Actually delete the test records.

    Args:
        confirm: Must be True to actually delete. Default False (extra safety).

    Pass confirm=True like:
        bench --site vitalvida.systemforce.ng execute \
          vitalvida.scripts.cleanup_test_data.execute --kwargs '{"confirm": true}'
    """
    if not confirm:
        print("\n⚠️  execute() called without confirm=True. NOT DELETING.")
        print("To actually delete, call: execute(confirm=True)")
        print("Or via bench: --kwargs '{\"confirm\": true}'")
        return

    print("\n" + "=" * 70)
    print("VITALVIDA TEST DATA CLEANUP — EXECUTING")
    print("=" * 70)
    print(f"Time: {now_datetime()}")
    print()

    findings = audit()

    deleted_counts = {}
    errors = []

    # Order matters: delete child records before parents
    deletion_order = [
        ("VV Order Nudge Log", findings.get("VV Order Nudge Log", [])),
        ("VV Order (attributed to test affiliates)", findings.get("VV Order (attributed to test affiliates)", [])),
        ("VV Order (test customers)", findings.get("VV Order (test customers)", [])),
        ("VV Media Buyer", findings.get("VV Media Buyer", [])),
        ("User", findings.get("User", [])),
    ]

    for label, records in deletion_order:
        # Normalize doctype name (strip parenthetical from VV Order labels)
        doctype = label.split(" (")[0]

        if not records:
            continue

        print(f"\nDeleting {len(records)} {doctype} records...")
        success = 0
        for name in records:
            try:
                # Don't actually delete Administrator or Guest even if matched
                if doctype == "User" and name in ("Administrator", "Guest"):
                    continue

                frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)
                success += 1
            except Exception as e:
                errors.append(f"{doctype} {name}: {str(e)[:200]}")
                print(f"  ✗ Failed: {name} → {str(e)[:100]}")

        deleted_counts[label] = success
        print(f"  ✓ Deleted {success} of {len(records)}")

    frappe.db.commit()

    # ── Summary ──
    print("\n" + "=" * 70)
    print("CLEANUP COMPLETE")
    print("=" * 70)
    total_deleted = sum(deleted_counts.values())
    print(f"Total records deleted: {total_deleted}")
    for label, count in deleted_counts.items():
        print(f"  {label}: {count}")

    if errors:
        print(f"\n⚠️  {len(errors)} errors occurred:")
        for err in errors[:10]:
            print(f"  - {err}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more")
    else:
        print("\n✓ No errors. Clean.")
    print()
