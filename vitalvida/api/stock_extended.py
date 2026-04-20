import frappe
from frappe import _
from datetime import datetime, timedelta

# ════════════════════════════════════════════════════════════
# LOGISTICS PORTAL APIs - Stock & Dispatch Management
# ════════════════════════════════════════════════════════════

@frappe.whitelist()
def dispatch_stock(dispatch_name=None, delivery_agent=None, items=None,
                   storekeeper_fee=0, da_pickup_transport=0, driver_transport=0,
                   driver_phone=None, motor_park=None, eta_date=None,
                   notes=None, approval_required=False):
    """Create or update a stock dispatch"""
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
    """Mark consignment as confirmed/delivered"""
    try:
        doc = frappe.get_doc("Consignment", consignment_id)
        doc.status = "Delivered"
        doc.confirmed_at = frappe.utils.now()
        doc.confirmed_by = frappe.session.user
        doc.save(ignore_permissions=True)
        frappe.db.commit()
        return {"success": True, "message": f"Consignment {consignment_id} confirmed"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@frappe.whitelist()
def process_return(return_id, status):
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
