import frappe
from frappe import _
from datetime import datetime, timedelta

@frappe.whitelist()
def dispatch_stock(dispatch_name=None, delivery_agent=None, items=None, 
                   storekeeper_fee=0, da_pickup_transport=0, driver_transport=0,
                   driver_phone=None, motor_park=None, eta_date=None,
                   notes=None, approval_required=False):
    """
    Create or update a Stock Dispatch with business logic validation.
    
    If approval_required=True, creates dispatch with status=Pending Approval
    and escalates to Operations Manager.
    
    Otherwise, creates with status=In Transit, decrements factory stock,
    and creates linked Consignment.
    """
    
    # Validate cost limits
    limits = frappe.get_value('Vitalvida Settings', 'Vitalvida Settings', 
                             ['max_storekeeper_fee', 'max_da_pickup_transport'])
    
    if float(storekeeper_fee) > float(limits[0]):
        if not approval_required:
            frappe.throw(_("Storekeeper fee exceeds limit. Resubmit with approval."))
    
    if float(da_pickup_transport) > float(limits[1]):
        if not approval_required:
            frappe.throw(_("DA pickup transport exceeds limit. Resubmit with approval."))
    
    # Check if DA is frozen
    da_frozen = frappe.db.get_value('DA Warehouse', 
                                    {'delivery_agent': delivery_agent},
                                    'is_frozen')
    if da_frozen:
        frappe.throw(_("Cannot dispatch to a frozen DA warehouse"))
    
    # Validate ETA (max 5 days)
    if eta_date:
        eta = datetime.strptime(eta_date, '%Y-%m-%d')
        today = datetime.now()
        days_until_eta = (eta - today).days
        if days_until_eta > 5:
            frappe.msgprint(_("⚠ ETA exceeds 5 days — flagged for review"), 
                          alert=True)
    
    # Handle approval flow
    if approval_required:
        doc = frappe.new_doc('Stock Dispatch')
        doc.delivery_agent = delivery_agent
        doc.dispatch_date = datetime.now().date()
        doc.eta_date = eta_date
        doc.driver_phone = driver_phone
        doc.motor_park = motor_park
        doc.storekeeper_fee = storekeeper_fee
        doc.da_pickup_transport = da_pickup_transport
        doc.driver_transport = driver_transport
        doc.total_cost = float(storekeeper_fee) + float(da_pickup_transport) + float(driver_transport)
        doc.notes = notes
        doc.approval_required = 1
        doc.status = 'Pending Approval'
        
        # Add items
        if items:
            for item in items:
                doc.append('items', {'product': item['product'], 'qty': item['qty']})
        
        doc.insert()
        frappe.db.commit()
        
        # Create escalation request
        esc = frappe.new_doc('Escalation Request')
        esc.type = 'Cost Approval'
        esc.subject = f"Dispatch {doc.name} — Cost Approval Required"
        esc.description = f"Storekeeper: ₦{storekeeper_fee}, DA Pickup: ₦{da_pickup_transport}"
        esc.linked_doctype = 'Stock Dispatch'
        esc.linked_name = doc.name
        esc.status = 'Open'
        esc.insert()
        frappe.db.commit()
        
        # Notify Operations Manager (placeholder)
        frappe.msgprint(_("Dispatch created and sent for approval to Operations Manager"))
        
        return {'dispatch_name': doc.name, 'status': 'Pending Approval', 'consignment_name': None}
    
    # Normal flow: create dispatch and ship immediately
    if dispatch_name:
        doc = frappe.get_doc('Stock Dispatch', dispatch_name)
    else:
        doc = frappe.new_doc('Stock Dispatch')
        doc.delivery_agent = delivery_agent
        doc.dispatch_date = datetime.now().date()
        doc.dispatched_by = frappe.session.user
    
    doc.eta_date = eta_date
    doc.driver_phone = driver_phone
    doc.motor_park = motor_park
    doc.storekeeper_fee = storekeeper_fee
    doc.da_pickup_transport = da_pickup_transport
    doc.driver_transport = driver_transport
    doc.total_cost = float(storekeeper_fee) + float(da_pickup_transport) + float(driver_transport)
    doc.notes = notes
    doc.status = 'In Transit'
    
    # Add items
    if items:
        doc.items = []
        for item in items:
            doc.append('items', {'product': item['product'], 'qty': item['qty']})
    
    doc.save()
    
    # Decrement factory stock (atomic)
    for item in items:
        stock_qty = frappe.db.get_value('Item', item['product'], 'stock_qty') or 0
        if float(item['qty']) > float(stock_qty):
            frappe.throw(_("Insufficient factory stock for {0}").format(item['product']))
        
        frappe.db.set_value('Item', item['product'], 'stock_qty', 
                           float(stock_qty) - float(item['qty']))
    
    # Create Consignment
    consignment = frappe.new_doc('Consignment')
    consignment.delivery_agent = delivery_agent
    consignment.dispatch_date = doc.dispatch_date
    consignment.eta_date = eta_date
    consignment.driver_phone = driver_phone
    consignment.linked_dispatch = doc.name
    consignment.status = 'Pending Receipt'
    
    for item in items:
        consignment.append('items', {'product': item['product'], 'qty': item['qty']})
    
    consignment.insert()
    frappe.db.commit()
    
    # Notify DA (placeholder)
    frappe.msgprint(_("Dispatch shipped and DA notified via WhatsApp"))
    
    return {
        'dispatch_name': doc.name, 
        'status': 'In Transit', 
        'consignment_name': consignment.name
    }

@frappe.whitelist()
def dispatch_stats():
    """
    Get dispatch statistics: pending, in_transit, delivered counts
    """
    pending = frappe.db.count('Stock Dispatch', {'status': 'Pending'})
    in_transit = frappe.db.count('Stock Dispatch', {'status': 'In Transit'})
    delivered = frappe.db.count('Stock Dispatch', {'status': 'Confirmed'})
    
    return {
        'pending': pending,
        'in_transit': in_transit,
        'delivered': delivered
    }
