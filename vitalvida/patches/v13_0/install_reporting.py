"""Package 13 Reporting — read-only projections. No writers, no consumers, no
event registration required. Installer is intentionally a no-op beyond a marker;
reporting reads ERPNext authorities + immutable events at query time.
"""
import frappe


def execute():
    # Read-only package: nothing to register. Presence of the module is the install.
    frappe.logger().info("Package 13 Reporting installed (read-only projections).")
