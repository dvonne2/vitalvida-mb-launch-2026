"""
Inventory Portal shell.

Serves the React inventory-hub SPA same-origin with a real Frappe session, so
that src/services/api.ts -> frappeCall() authenticates with the sid cookie and
a valid CSRF token.

WHY THIS FILE EXISTS
    A static index.html cannot obtain a CSRF token. Frappe emits one only into
    pages it renders itself. Without it, every POST from the SPA is rejected
    with CSRFTokenError, no matter what allow_cors says. This page renders the
    token into window.csrf_token -- which is exactly what api.ts reads.

    It also sets window.__VV_LIVE__ = true, the escape hatch api.ts already has
    for forcing live mode without an env rebuild.

ROUTES   /inventory-portal  and any sub-path (see website_route_rules in hooks.py)
ASSETS   /assets/vitalvida/inventory-portal/  <-  vitalvida/public/inventory-portal/

AUTH     Normal Frappe login. Guests are redirected to /login. No magic link,
         no allow_guest. frappe.session.user reaches api/inventory.py::_guard()
         unchanged.
"""

import json
import os

import frappe
from frappe.sessions import get_csrf_token

# The CSRF token is per-session. This page must never be cached.
no_cache = 1

ALLOWED_ROLES = ("Inventory Manager", "Operations Manager", "System Manager")

_BUNDLE_DIR = "inventory-portal"
_ASSET_BASE = f"/assets/vitalvida/{_BUNDLE_DIR}"


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.local.flags.redirect_location = "/login?redirect-to=/inventory-portal"
        raise frappe.Redirect

    roles = frappe.get_roles(frappe.session.user)
    if not any(r in roles for r in ALLOWED_ROLES):
        frappe.throw(
            "Access denied. Inventory Manager role required.",
            frappe.PermissionError,
        )

    css_files, js_files = _bundle_assets()

    context.no_cache = 1
    context.csrf_token = get_csrf_token()
    context.session_user = frappe.session.user
    context.asset_base = _ASSET_BASE
    context.css_files = css_files
    context.js_files = js_files
    context.bundle_present = bool(js_files)
    return context


def _bundle_assets():
    """Resolve Vite's hashed filenames from its build manifest.

    Vite >= 5 writes .vite/manifest.json; earlier versions write manifest.json.
    Returns ([css_urls], [js_urls]) -- empty lists when no bundle is deployed,
    so the page still renders and the session/CSRF chain stays testable.
    """
    root = frappe.get_app_path("vitalvida", "public", _BUNDLE_DIR)

    for rel in (os.path.join(".vite", "manifest.json"), "manifest.json"):
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            continue
        try:
            with open(path) as fh:
                manifest = json.load(fh)
        except Exception:
            frappe.log_error(frappe.get_traceback(), "Inventory Portal manifest")
            return [], []

        entry = next((v for v in manifest.values() if v.get("isEntry")), None)
        if not entry or not entry.get("file"):
            return [], []

        js = [f"{_ASSET_BASE}/{entry['file']}"]
        css = [f"{_ASSET_BASE}/{c}" for c in entry.get("css", [])]
        return css, js

    return [], []
