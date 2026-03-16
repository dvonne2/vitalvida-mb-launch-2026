"""
M32 — Media Buyer Dashboard (Script Report) — TK Store Model
All buyers ranked by orders delivered. Shows commission, payout status, commitment fee.
No CPP/clawback columns — buyer funds their own ads.
"""
import frappe
from frappe.utils import get_first_day_of_week, today, add_days

def execute(filters=None):
    columns = get_columns()
    data = get_data(filters)
    return columns, data

def get_columns():
    return [
        {"fieldname":"rank","label":"Rank","fieldtype":"Int","width":50},
        {"fieldname":"buyer_name","label":"Buyer","fieldtype":"Data","width":160},
        {"fieldname":"platform","label":"Platform","fieldtype":"Data","width":80},
        {"fieldname":"orders_generated","label":"Generated","fieldtype":"Int","width":90},
        {"fieldname":"orders_delivered","label":"Delivered","fieldtype":"Int","width":90},
        {"fieldname":"commission_per_order","label":"Rate/Order","fieldtype":"Currency","width":100},
        {"fieldname":"gross_commission","label":"Commission","fieldtype":"Currency","width":120},
        {"fieldname":"net_payout","label":"Net Payout","fieldtype":"Currency","width":120},
        {"fieldname":"commitment_fee","label":"Fee Status","fieldtype":"Data","width":90},
        {"fieldname":"lifetime_orders","label":"Lifetime","fieldtype":"Int","width":80},
        {"fieldname":"consecutive_zero","label":"Zero Weeks","fieldtype":"Int","width":80},
        {"fieldname":"status","label":"Report Status","fieldtype":"Data","width":110},
    ]

def get_data(filters):
    week = (filters or {}).get("week")
    if not week:
        week = str(add_days(get_first_day_of_week(today()), -7))
    platform = (filters or {}).get("platform", "")

    reports = frappe.get_all("VV Media Buyer Weekly Report",
        filters={"week_start": week},
        fields=["media_buyer","orders_generated","orders_delivered",
                "commission_per_order","gross_commission","net_payout","status"],
        order_by="orders_delivered desc")

    data = []
    for r in reports:
        buyer = frappe.db.get_value("VV Media Buyer", r.media_buyer,
            ["full_name","platform","commitment_fee_status",
             "total_lifetime_orders","consecutive_zero_weeks"], as_dict=True)
        if not buyer:
            continue
        if platform and buyer.platform != platform and buyer.platform != "Both":
            continue

        data.append({
            "buyer_name": buyer.full_name,
            "platform": buyer.platform,
            "orders_generated": r.orders_generated,
            "orders_delivered": r.orders_delivered,
            "commission_per_order": float(r.commission_per_order or 0),
            "gross_commission": float(r.gross_commission or 0),
            "net_payout": float(r.net_payout or 0),
            "commitment_fee": buyer.commitment_fee_status or "Unpaid",
            "lifetime_orders": int(buyer.total_lifetime_orders or 0),
            "consecutive_zero": int(buyer.consecutive_zero_weeks or 0),
            "status": r.status,
        })

    data.sort(key=lambda x: x["orders_delivered"], reverse=True)
    for i, row in enumerate(data, 1):
        row["rank"] = i
    return data
