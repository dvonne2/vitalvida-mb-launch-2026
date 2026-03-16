"""M27 — Profit First Compliance Report. Target % vs Actual % per wallet."""
import frappe

def execute(filters=None):
    columns = [
        {"fieldname":"bucket_name","label":"Wallet","fieldtype":"Data","width":160},
        {"fieldname":"target_pct","label":"Target %","fieldtype":"Percent","width":100},
        {"fieldname":"current_balance","label":"Balance","fieldtype":"Currency","width":140},
        {"fieldname":"actual_pct","label":"Actual %","fieldtype":"Percent","width":100},
        {"fieldname":"variance_pct","label":"Variance","fieldtype":"Percent","width":100},
        {"fieldname":"status","label":"Status","fieldtype":"Data","width":100},
    ]

    buckets = frappe.get_all("Profit First Bucket", filters={"is_active": 1},
        fields=["bucket_name","allocation_percentage","current_balance"], order_by="bucket_name asc")

    total_balance = sum(float(b.current_balance or 0) for b in buckets) or 1
    data = []
    within_target = 0
    for b in buckets:
        balance = float(b.current_balance or 0)
        target = float(b.allocation_percentage or 0)
        actual = round(balance / total_balance * 100, 1)
        variance = round(actual - target, 1)
        status = "On Target" if abs(variance) <= 5 else ("Over" if variance > 0 else "Under")
        if abs(variance) <= 5:
            within_target += 1
        data.append({"bucket_name": b.bucket_name, "target_pct": target,
            "current_balance": balance, "actual_pct": actual,
            "variance_pct": variance, "status": status})

    return columns, data
