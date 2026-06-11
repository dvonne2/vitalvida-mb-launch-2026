import frappe
from frappe import _
from datetime import datetime, timedelta

# ════════════════════════════════════════════════════════════
# LOGISTICS PORTAL APIs - Stock & Dispatch Management
# ════════════════════════════════════════════════════════════


def _require_logistics():
    """FIX BUG 3: Auth guard mirroring logistics.py pattern.
    Restricts these endpoints to Logistics/Ops roles only.
    """
    user = frappe.session.user
    roles = frappe.get_roles(user)
    allowed = ["Logistics Manager", "Logistics User", "Operations Manager", "System Manager"]
    if not any(r in roles for r in allowed):
        return {"error": "Access denied. Logistics role required.", "code": 403}
    return None


@frappe.whitelist()
def dispatch_stock(dispatch_name=None, delivery_agent=None, items=None,
                   storekeeper_fee=0, da_pickup_transport=0, driver_transport=0,
                   driver_phone=None, motor_park=None, eta_date=None,
                   notes=None, approval_required=False):
    """Create or update a stock dispatch"""
    guard = _require_logistics()
    if guard:
        return guard
    try:
        settings = frappe.get_single("Vitalvida Settings")
        
        # Validation
        if not approval_required:
            if float(storekeeper_fee) > settings.max_storekeeper_fee:
                frappe.throw(_("Storekeeper fee exceeds limit"))
            if float(da_pickup_transport) > settings.max_da_pickup_transport:
                frappe.throw(_("DA transport fee exceeds limit"))
        
        # Check DA is not frozen
        da_warehouse = frappe.get_doc("DA Warehouse", delivery_agent)
        if da_warehouse.is_frozen:
            frappe.throw(_("Cannot dispatch to frozen DA warehouse"))
        
        # Create dispatch
        if not dispatch_name:
            doc = frappe.new_doc("Stock Dispatch")
            doc.delivery_agent = delivery_agent
            doc.dispatch_date = datetime.now().date()
            doc.eta_date = eta_date
            doc.driver_phone = driver_phone
            doc.motor_park = motor_park
            doc.status = "Pending"
            doc.approval_required = approval_required
        else:
            doc = frappe.get_doc("Stock Dispatch", dispatch_name)
        
        # Add items
        doc.items = []
        if items:
            for item in items:
                doc.append("items", {
                    "product": item.get("product"),
                    "qty": item.get("qty")
                })
        
        # Set costs
        doc.storekeeper_fee = storekeeper_fee
        doc.da_pickup_transport = da_pickup_transport
        doc.driver_transport = driver_transport
        doc.total_cost = float(storekeeper_fee) + float(da_pickup_transport) + float(driver_transport)
        doc.notes = notes
        doc.dispatched_by = frappe.session.user
        
        doc.save(ignore_permissions=True)
        
        # Create consignment automatically
        consignment = frappe.new_doc("Consignment")
        consignment.delivery_agent = delivery_agent
        consignment.dispatch_date = doc.dispatch_date
        consignment.eta_date = eta_date
        consignment.linked_dispatch = doc.name
        consignment.status = "Pending"
        consignment.save(ignore_permissions=True)
        
        frappe.db.commit()
        return {"success": True, "dispatch_id": doc.name}
    except Exception as e:
        frappe.log_error(str(e), "dispatch_stock")
        return {"success": False, "error": str(e)}

@frappe.whitelist()
def dispatch_stats():
    guard = _require_logistics()
    if guard:
        return guard
    """Get dispatch statistics"""
    pending = frappe.db.count("Stock Dispatch", {"status": "Pending"})
    in_transit = frappe.db.count("Stock Dispatch", {"status": "In Transit"})
    delivered = frappe.db.count("Stock Dispatch", {"status": "Delivered"})
    
    return {
        "pending": pending,
        "in_transit": in_transit,
        "delivered": delivered
    }

@frappe.whitelist()
def get_da_warehouse(delivery_agent):
    guard = _require_logistics()
    if guard:
        return guard
    """Get DA warehouse stock info"""
    try:
        warehouse = frappe.get_doc("DA Warehouse", delivery_agent)
        return {
            "name": warehouse.name,
            "current_stock": warehouse.current_stock,
            "is_frozen": warehouse.is_frozen,
            "freeze_reason": warehouse.freeze_reason
        }
    except Exception as e:
        return {"error": str(e)}

@frappe.whitelist()
def unfreeze_da_warehouse(delivery_agent):
    guard = _require_logistics()
    if guard:
        return guard
    """Unfreeze a DA warehouse"""
    try:
        warehouse = frappe.get_doc("DA Warehouse", delivery_agent)
        warehouse.is_frozen = False
        warehouse.freeze_reason = ""
        warehouse.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "message": f"DA {delivery_agent} unfrozen"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@frappe.whitelist()
def confirm_consignment(consignment_id):
    guard = _require_logistics()
    if guard:
        return guard
    """
    Mark consignment as confirmed/delivered and add stock to DA Warehouse.

    FIX ISSUE 14: Old code only set status='Delivered' on the Consignment doc
    but never added items to DA Warehouse. DA stock never increased on delivery.
    Now reads items from the linked Stock Dispatch and credits DA Warehouse
    for each product received.
    """
    try:
        doc = frappe.get_doc("Consignment", consignment_id)
        if doc.status == "Delivered":
            return {"success": False, "error": "Consignment already confirmed."}

        doc.status = "Delivered"
        doc.confirmed_at = frappe.utils.now()
        doc.confirmed_by = frappe.session.user
        doc.save(ignore_permissions=True)

        # FIX: Credit DA Warehouse for each item in the linked Stock Dispatch
        if doc.linked_dispatch:
            try:
                dispatch = frappe.get_doc("Stock Dispatch", doc.linked_dispatch)
                now = frappe.utils.now_datetime()

                for item in (dispatch.items or []):
                    product = item.get("product") or item.product
                    qty = float(item.get("qty") or item.qty or 0)
                    if not product or qty <= 0:
                        continue

                    # Get or create DA Warehouse record for this product
                    warehouse_name = frappe.db.exists("DA Warehouse", {
                        "delivery_agent": doc.delivery_agent,
                        "product": product,
                    })
                    if warehouse_name:
                        current = float(
                            frappe.db.get_value("DA Warehouse", warehouse_name, "current_stock") or 0
                        )
                        frappe.db.set_value("DA Warehouse", warehouse_name, {
                            "current_stock": current + qty,
                            "last_updated": now,
                        })
                    else:
                        frappe.get_doc({
                            "doctype": "DA Warehouse",
                            "delivery_agent": doc.delivery_agent,
                            "product": product,
                            "current_stock": qty,
                            "is_frozen": 0,
                            "last_updated": now,
                        }).insert(ignore_permissions=True)

                    # FIX BUG 17: Correct field names + valid entry_type.
                    # Old code used "quantity_change" (field doesn't exist) and
                    # entry_type "Receipt" (not in doctype's allowed literals).
                    # Both errors were silently swallowed by the try/except,
                    # leaving no DA Stock Entry record for consignment receipts.
                    # Use the canonical _create_stock_entry helper instead.
                    try:
                        from vitalvida.stock import _create_stock_entry
                        _create_stock_entry(
                            delivery_agent=doc.delivery_agent,
                            product=product,
                            entry_type="Dispatch",
                            direction="In",
                            quantity=qty,
                            reference_dispatch=doc.linked_dispatch,
                            notes=f"Consignment {consignment_id} confirmed",
                        )
                    except Exception as entry_err:
                        frappe.log_error(str(entry_err), "confirm_consignment Stock Entry Error")

                # Mark dispatch as Received
                frappe.db.set_value("Stock Dispatch", doc.linked_dispatch, "status", "Received")

            except Exception as stock_err:
                frappe.log_error(
                    f"confirm_consignment: stock update failed for "
                    f"{consignment_id}: {str(stock_err)}",
                    "confirm_consignment Stock Error"
                )

        frappe.db.commit()
        return {"success": True, "message": f"Consignment {consignment_id} confirmed"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "confirm_consignment Error")
        return {"success": False, "error": str(e)}

@frappe.whitelist()
def process_return(return_id, status):
    guard = _require_logistics()
    if guard:
        return guard
    """Process DA stock return"""
    try:
        doc = frappe.get_doc("DA Stock Return", return_id)
        doc.status = status  # Pending, Approved, Rejected
        
        if status == "Approved":
            doc.approved_by = frappe.session.user
            doc.approved_at = frappe.utils.now()
        elif status == "Rejected":
            doc.processed_by = frappe.session.user
            doc.processed_at = frappe.utils.now()
        
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "message": f"Return {return_id} processed"}
    except Exception as e:
        return {"success": False, "error": str(e)}

