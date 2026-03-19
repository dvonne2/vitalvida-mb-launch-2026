import frappe

single_doctypes = ["System Settings","Accounts Settings","HR Settings","Stock Settings","Buying Settings","Selling Settings","Website Settings","Email Settings","Print Settings","Notification Settings"]

for ws_name in ["Build", "ERPNext Settings", "Systemforce Settings"]:
    try:
        if not frappe.db.exists("Workspace", ws_name):
            print("Not found: " + ws_name)
            continue
        ws = frappe.get_doc("Workspace", ws_name)
        before = len(ws.shortcuts)
        ws.shortcuts = [s for s in ws.shortcuts if s.link_to not in single_doctypes]
        ws.save(ignore_permissions=True)
        print("Fixed " + ws_name + " removed " + str(before - len(ws.shortcuts)))
    except Exception as e:
        print("Error " + ws_name + ": " + str(e))

frappe.db.commit()
print("All done!")
