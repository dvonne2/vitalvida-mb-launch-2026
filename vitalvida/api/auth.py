"""
vitalvida/api/auth.py

FIX BUG 8: Consolidated login endpoint.
Previously login() was duplicated in finance.py, owner.py, and investor.py
with slightly different ROLE_PORTAL mappings. Any change (e.g. adding 2FA,
rate limiting, audit logging) had to be done in 3 places.

All portals now import login and check_session from here.
The individual login() functions in finance.py, owner.py, investor.py
now call this one — keeping backward compatibility.

Usage in each portal file:
    from vitalvida.api.auth import login, check_session
"""

import frappe


# Full role → portal mapping used across all portals
ROLE_PORTAL = {
    "Finance Controller":   "finance",
    "Accountant":           "finance",
    "Owner":                "owner",
    "System Manager":       "finance",
    "Operations Manager":   "operations",
    "Delivery Agent":       "da",
    "Telesales Closer":     "telesales",
    "Media Buyer":          "media_buyer",
    "Logistics":            "logistics",
    "Inventory Manager":    "inventory",
    "Investor":             "investor",
}


@frappe.whitelist(allow_guest=True)
def login(usr, pwd):
    """
    Shared login endpoint for all VitalVida portals.
    Authenticates with ERPNext credentials and returns session info
    including which portal the user should be redirected to.
    """
    try:
        from frappe.auth import LoginManager
        lm = LoginManager()
        lm.authenticate(user=usr, pwd=pwd)
        lm.post_login()

        user     = frappe.session.user
        roles    = frappe.get_roles(user)
        fullname = frappe.db.get_value("User", user, "full_name") or user
        portal   = next((ROLE_PORTAL[r] for r in ROLE_PORTAL if r in roles), None)

        return {
            "success": True,
            "user":    user,
            "name":    fullname,
            "portal":  portal,
            "roles":   roles,
        }
    except frappe.AuthenticationError:
        return {"success": False, "error": "Invalid email or password"}
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "VitalVida Login Error")
        return {"success": False, "error": str(e)}


@frappe.whitelist(allow_guest=True)
def check_session():
    """
    Returns current session state.
    Called by React portals on load to check if user is still logged in.
    """
    try:
        user = frappe.session.user
        if not user or user == "Guest":
            return {"authenticated": False}

        roles    = frappe.get_roles(user)
        fullname = frappe.db.get_value("User", user, "full_name") or user
        portal   = next((ROLE_PORTAL[r] for r in ROLE_PORTAL if r in roles), None)

        return {
            "authenticated": True,
            "user":   user,
            "name":   fullname,
            "portal": portal,
            "roles":  roles,
        }
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "check_session Error")
        return {"authenticated": False, "error": str(e)}

