import frappe

def create_sales_order_doctype():
    if not frappe.db.exists("DocType", "Sales Order"):
        doc = frappe.get_doc({
            "doctype": "DocType",
            "name": "Sales Order",
            "module": "My App",
            "custom": 1,
            "is_single": 0,
            "fields": [
                {"fieldname": "order_id", "label": "Order ID", "fieldtype": "Data", "reqd": 1},
                {"fieldname": "customer_name", "label": "Customer Name", "fieldtype": "Data", "reqd": 1},
                {"fieldname": "total_amount", "label": "Total Amount", "fieldtype": "Currency"},
            ],
            "permissions": [{"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1}]
        })
        doc.insert()
        frappe.db.commit()
        print("Sales Order DocType created!")
