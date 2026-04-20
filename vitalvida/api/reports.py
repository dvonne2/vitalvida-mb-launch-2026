import frappe
from frappe import _
from datetime import datetime, timedelta

# ════════════════════════════════════════════════════════════
# OWNER DASHBOARD APIs - Reporting & Analytics
# ════════════════════════════════════════════════════════════

@frappe.whitelist()
def get_revenue_stats():
    """Get revenue statistics"""
    today = datetime.now().date()
    
    # Get delivered orders
    delivered = frappe.get_list("Order",
        filters={"order_status": "Delivered", "delivered_at": f">={today}"},
        fields=["total_payable", "product_amount", "delivery_fee"]
    )
    
    total_revenue = sum([o.get("total_payable", 0) for o in delivered])
    product_revenue = sum([o.get("product_amount", 0) for o in delivered])
    delivery_revenue = sum([o.get("delivery_fee", 0) for o in delivered])
    
    order_count = frappe.db.count("Order")
    delivered_count = len(delivered)
    
    profit = total_revenue - (product_revenue * 0.3)  # Assuming 30% cost
    
    return {
        "total_revenue": total_revenue,
        "product_revenue": product_revenue,
        "delivery_revenue": delivery_revenue,
        "order_count": order_count,
        "delivered_count": delivered_count,
        "profit": profit,
        "profit_margin": (profit / total_revenue * 100) if total_revenue > 0 else 0
    }

@frappe.whitelist()
def get_profit_first_wallets():
    """Get Profit First wallet allocations"""
    wallets = frappe.get_list("Profit First Wallet",
        fields=["name", "amount", "allocated_percentage", "current_balance", "wallet_type"]
    )
    
    total_allocated = sum([w.get("amount", 0) for w in wallets])
    
    return {
        "wallets": wallets,
        "total_allocated": total_allocated,
        "total_available": get_revenue_stats().get("profit", 0)
    }

@frappe.whitelist()
def get_da_leaderboard():
    """Get top delivery agents by performance"""
    das = frappe.get_list("DA Warehouse",
        fields=["name", "delivery_agent", "current_stock", "dsr_percentage"],
        limit_page_length=10,
        order_by="dsr_percentage desc"
    )
    
    return das

@frappe.whitelist()
def get_telesales_leaderboard():
    """Get top closers by revenue"""
    from datetime import datetime, timedelta
    
    today = datetime.now().date()
    week_start = today - timedelta(days=today.weekday())
    
    # Get orders by closer with revenue
    orders = frappe.get_list("Order",
        filters={"creation": [">", week_start]},
        fields=["telesales_closer", "total_payable", "order_status"],
        limit_page_length=100
    )
    
    # Group by closer
    closer_revenue = {}
    for order in orders:
        closer = order.get("telesales_closer")
        if closer not in closer_revenue:
            closer_revenue[closer] = {"revenue": 0, "orders": 0, "delivered": 0}
        
        closer_revenue[closer]["revenue"] += order.get("total_payable", 0)
        closer_revenue[closer]["orders"] += 1
        if order.get("order_status") == "Delivered":
            closer_revenue[closer]["delivered"] += 1
    
    # Sort by revenue
    leaderboard = sorted(
        [{"closer": k, **v} for k, v in closer_revenue.items()],
        key=lambda x: x["revenue"],
        reverse=True
    )
    
    return leaderboard[:10]

@frappe.whitelist()
def get_media_buyer_leaderboard():
    """Get top media buyers"""
    # Similar structure - can be extended based on media buyer field in Order
    return {
        "note": "Media buyer tracking to be implemented",
        "buyers": []
    }

@frappe.whitelist()
def get_stock_positions():
    """Get inventory valuation by product"""
    # Get all products with their stock levels
    products = frappe.get_list("Product",
        fields=["name", "product_name", "unit_price"],
        limit_page_length=100
    )
    
    stock_positions = []
    for product in products:
        stock_qty = frappe.db.get_value(
            "Stock Dispatch Item",
            {"product": product.get("name")},
            "SUM(qty) as total"
        )
        
        stock_positions.append({
            "product": product.get("name"),
            "product_name": product.get("product_name"),
            "quantity": stock_qty[0] if stock_qty else 0,
            "unit_price": product.get("unit_price", 0),
            "total_value": (stock_qty[0] if stock_qty else 0) * product.get("unit_price", 0)
        })
    
    return stock_positions

@frappe.whitelist()
def get_escalations():
    """Get pending escalation requests"""
    escalations = frappe.get_list("Escalation Request",
        filters={"status": "Pending"},
        fields=["name", "dispatch", "reason", "created_by", "creation"],
        order_by="creation desc",
        limit_page_length=20
    )
    
    return escalations

@frappe.whitelist()
def get_unit_economics():
    """Get cost breakdown and unit economics"""
    today = datetime.now().date()
    week_start = today - timedelta(days=7)
    
    # Get delivered orders for this week
    orders = frappe.get_list("Order",
        filters={"order_status": "Delivered", "delivered_at": [">", week_start]},
        fields=["name", "total_payable", "delivery_fee", "product_amount"]
    )
    
    total_orders = len(orders)
    if total_orders == 0:
        return {"error": "No data for this period"}
    
    total_revenue = sum([o.get("total_payable", 0) for o in orders])
    total_delivery_cost = sum([o.get("delivery_fee", 0) for o in orders])
    total_product_cost = sum([o.get("product_amount", 0) for o in orders])
    
    avg_order_value = total_revenue / total_orders if total_orders > 0 else 0
    avg_delivery_cost = total_delivery_cost / total_orders if total_orders > 0 else 0
    avg_product_cost = total_product_cost / total_orders if total_orders > 0 else 0
    
    profit_per_order = avg_order_value - avg_delivery_cost - (avg_product_cost * 0.3)
    
    return {
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "total_delivery_cost": total_delivery_cost,
        "total_product_cost": total_product_cost,
        "avg_order_value": round(avg_order_value, 2),
        "avg_delivery_cost": round(avg_delivery_cost, 2),
        "avg_product_cost": round(avg_product_cost, 2),
        "profit_per_order": round(profit_per_order, 2),
        "total_profit": round(profit_per_order * total_orders, 2)
    }
