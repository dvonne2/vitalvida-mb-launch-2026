import frappe

def get_context(context):
    if frappe.session.user == 'Guest':
        frappe.throw('Not permitted', frappe.PermissionError)
    context.closer_name = frappe.session.user
    context.no_cache = 1
