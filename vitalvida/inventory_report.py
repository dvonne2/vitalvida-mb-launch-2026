"""
M20 — Daily National Inventory Email Report
inventory_report.py

send_daily_inventory_report() runs every day at 7:00 AM WAT via cron.
Generates and emails a national stock health report.
"""

import frappe
from frappe.utils import now_datetime, today


def send_daily_inventory_report() -> None:
    """
    Runs daily at 7:00 AM WAT via cron: 0 7 * * *
    Generates stock health report and emails to configured recipient.
    """
    settings = frappe.get_single("VitalVida Settings")
    recipient = getattr(settings, "inventory_report_email", None)

    if not recipient:
        frappe.log_error(
            "M20: No inventory_report_email configured in Vitalvida Settings.",
            "M20 Inventory Report Skipped"
        )
        return

    try:
        report = _generate_report()
        _send_email(recipient, report)
    except Exception as e:
        frappe.log_error(
            f"M20: Daily inventory report failed: {str(e)}",
            "M20 Inventory Report Error"
        )


def _generate_report() -> dict:
    """Generate the national stock health report data."""
    now = now_datetime()

    # Get all products with warehouse stock
    warehouses = frappe.db.sql("""
        SELECT
            dw.product,
            COALESCE(i.item_name, dw.product) as product_name,
            SUM(dw.current_stock) as total_stock,
            COUNT(DISTINCT dw.delivery_agent) as da_count,
            SUM(CASE WHEN dw.is_frozen = 1 THEN 1 ELSE 0 END) as frozen_count
        FROM `tabDA Warehouse` dw
        LEFT JOIN `tabItem` i ON i.name = dw.product
        GROUP BY dw.product
        ORDER BY total_stock ASC
    """, as_dict=True)

    out_of_stock = []
    low_stock = []
    well_stocked = []

    for w in warehouses:
        stock = float(w.total_stock or 0)
        # Get threshold from Item if available
        threshold = float(
            frappe.db.get_value("Item", w.product, "safety_stock") or 10
        )

        item = {
            "product": w.product,
            "product_name": w.product_name,
            "total_stock": stock,
            "da_count": w.da_count,
            "frozen_count": w.frozen_count,
            "threshold": threshold,
        }

        if stock <= 0:
            out_of_stock.append(item)
        elif stock <= threshold:
            low_stock.append(item)
        else:
            well_stocked.append(item)

    total_products = len(warehouses)
    total_stock_value = sum(float(w.total_stock or 0) for w in warehouses)

    # Critical alerts: products at zero
    critical_alerts = []
    for item in out_of_stock:
        critical_alerts.append(
            f"⚠ {item['product_name']} — ZERO STOCK across {item['da_count']} DAs"
        )

    # Recommendations
    recommendations = []
    for item in low_stock:
        recommendations.append(
            f"📦 Reorder {item['product_name']} — {item['total_stock']:.0f} units "
            f"remaining (threshold: {item['threshold']:.0f})"
        )

    return {
        "date": today(),
        "generated_at": str(now),
        "total_products": total_products,
        "total_stock_units": total_stock_value,
        "out_of_stock_count": len(out_of_stock),
        "low_stock_count": len(low_stock),
        "well_stocked_count": len(well_stocked),
        "out_of_stock": out_of_stock,
        "low_stock": low_stock,
        "well_stocked": well_stocked,
        "critical_alerts": critical_alerts,
        "recommendations": recommendations,
    }


def _send_email(recipient: str, report: dict) -> None:
    """Format and send the inventory report email."""
    subject = f"VitalVida Daily Inventory Report — {report['date']}"

    # Build HTML body
    lines = [
        f"<h2>National Inventory Report — {report['date']}</h2>",
        f"<p>Generated: {report['generated_at']}</p>",
        "<hr>",
        "<h3>Summary</h3>",
        f"<p>Total Products Tracked: <strong>{report['total_products']}</strong></p>",
        f"<p>Total Stock Units: <strong>{report['total_stock_units']:.0f}</strong></p>",
        f"<p>🔴 Out of Stock: <strong>{report['out_of_stock_count']}</strong></p>",
        f"<p>🟡 Low Stock: <strong>{report['low_stock_count']}</strong></p>",
        f"<p>🟢 Well Stocked: <strong>{report['well_stocked_count']}</strong></p>",
    ]

    if report["critical_alerts"]:
        lines.append("<hr><h3>🚨 Critical Alerts</h3>")
        for alert in report["critical_alerts"]:
            lines.append(f"<p>{alert}</p>")

    if report["recommendations"]:
        lines.append("<hr><h3>Recommendations</h3>")
        for rec in report["recommendations"]:
            lines.append(f"<p>{rec}</p>")

    if report["low_stock"]:
        lines.append("<hr><h3>Low Stock Detail</h3>")
        lines.append("<table border='1' cellpadding='6' cellspacing='0' style='border-collapse:collapse;'>")
        lines.append("<tr><th>Product</th><th>Stock</th><th>Threshold</th><th>DAs</th></tr>")
        for item in report["low_stock"]:
            lines.append(
                f"<tr><td>{item['product_name']}</td>"
                f"<td>{item['total_stock']:.0f}</td>"
                f"<td>{item['threshold']:.0f}</td>"
                f"<td>{item['da_count']}</td></tr>"
            )
        lines.append("</table>")

    body = "\n".join(lines)

    frappe.sendmail(
        recipients=[recipient],
        subject=subject,
        message=body,
        now=True,
    )

    frappe.log_error(
        f"M20: Daily inventory report sent to {recipient}",
        "M20 Inventory Report Sent"
    )
