import frappe
from frappe.utils import now_datetime


def variance_check(stock_count_name: str) -> None:
    """
    Called from StockCount.on_submit().
    CRITICAL: Only runs if count_status == "Confirmed". Disputed = blocked.
    """
    count = frappe.get_doc("Stock Count", stock_count_name)

    count_status = getattr(count, "count_status", None)
    if count_status and count_status != "Confirmed":
        frappe.log_error(
            f"M15: variance_check() blocked for {stock_count_name} "
            f"— count_status='{count_status}', not Confirmed.",
            "M15 Variance Blocked"
        )
        return

    delivery_agent = count.delivery_agent
    product = count.product
    counted_stock = float(
        getattr(count, "final_counted_quantity", None)
        or getattr(count, "counted_quantity", None)
        or 0
    )

    warehouse_name = frappe.db.exists("DA Warehouse", {
        "delivery_agent": delivery_agent,
        "product": product
    })
    system_stock = float(
        frappe.db.get_value("DA Warehouse", warehouse_name, "current_stock") or 0
    ) if warehouse_name else 0.0

    settings = frappe.get_single("VitalVida Settings")
    variance, variance_percent, status = _calculate_variance(
        system_stock, counted_stock, settings
    )

    variance_name = _create_variance_record(
        stock_count=stock_count_name,
        delivery_agent=delivery_agent,
        product=product,
        system_stock=system_stock,
        counted_stock=counted_stock,
        variance=variance,
        variance_percent=variance_percent,
        status=status
    )

    if status == "Critical":
        _alert_operations(variance_name, delivery_agent, product,
                          system_stock, counted_stock, variance_percent, status)
        try:
            from vitalvida.freeze import freeze_da_warehouse
            freeze_da_warehouse(
                delivery_agent,
                product,
                reason=f"Critical stock variance: {variance_percent:.1f}% on {product}"
            )
        except ImportError:
            frappe.log_error(
                f"M15: freeze_da_warehouse() not available. "
                f"DA={delivery_agent}, product={product}.",
                "M15 Freeze Skipped"
            )
        except Exception as e:
            frappe.log_error(
                f"M15: freeze_da_warehouse() failed for DA={delivery_agent}: {str(e)}",
                "M15 Freeze Error"
            )


def _calculate_variance(system_stock: float, counted_stock: float, settings) -> tuple:
    """Zero tolerance (Loop 2.1 / Step 13): ANY non-zero variance is Critical.
    Tolerance is NOT configurable. The settings parameter is retained for
    signature compatibility but is no longer read."""
    variance = system_stock - counted_stock
    variance_percent = (abs(variance) / system_stock * 100) if system_stock > 0 else 0.0
    status = "Critical" if variance != 0 else "Clean"
    return variance, variance_percent, status


def _create_variance_record(stock_count, delivery_agent, product,
                             system_stock, counted_stock, variance,
                             variance_percent, status) -> str:
    doc = frappe.get_doc({
        "doctype": "Stock Variance",
        "stock_count": stock_count,
        "delivery_agent": delivery_agent,
        "product": product,
        "system_stock": system_stock,
        "counted_stock": counted_stock,
        "variance": variance,
        "variance_percent": variance_percent,
        "variance_status": status,
        "checked_at": now_datetime(),
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def _alert_operations(variance_name, delivery_agent, product,
                       system_stock, counted_stock, variance_percent, status) -> None:
    try:
        from vitalvida.notifications import send_notification
        da_name = frappe.db.get_value("Delivery Agent", delivery_agent, "agent_name") or delivery_agent
        item_name = frappe.db.get_value("Item", product, "item_name") or product
        stub = frappe._dict({
            "name": variance_name,
            "customer_name": da_name,
            "customer_phone": "",
            "total_payable": 0,
            "package_contents": item_name,
            "address": "",
            "delivery_agent_name": da_name,
            "product": item_name,
            "system_stock": system_stock,
            "counted_stock": counted_stock,
            "variance_percent": round(variance_percent, 1),
            "variance_status": status,
        })
        send_notification(stub, event="StockVarianceAlert", recipient_type="Owner",
                          sender_channel="Transactional")
    except Exception as e:
        frappe.log_error(
            f"M14 alert failed for variance {variance_name}: {str(e)}", "M14 Alert Error"
        )
