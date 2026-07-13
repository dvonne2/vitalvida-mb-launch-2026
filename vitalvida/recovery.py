import frappe
from frappe.utils import now_datetime

FINANCE_ROLES = {"Finance User", "Finance Manager", "System Manager"}


def open_recovery_case(order_name):
    """Open a cash Recovery Case for an order whose verification deadline passed."""
    existing = frappe.db.exists("Recovery Case", {"order": order_name, "state": ["in", ["OPEN", "ACTIVE RECOVERY"]]})
    if existing:
        from vitalvida.domain.orders import transition
        transition(order_name, "Payment Recovery")
        frappe.db.commit()
        return
    o = frappe.db.get_value("VV Order", order_name, ["total_payable", "released_by"], as_dict=True) or frappe._dict()
    proof = frappe.db.get_value("Payment Proof", {"order": order_name}, "name")
    case = frappe.get_doc({
        "doctype": "Recovery Case",
        "order": order_name,
        "asset_type": "Cash",
        "amount": o.get("total_payable") or 0,
        "last_known_custodian": o.get("released_by"),
        "expected_custodian": "Company",
        "evidence": proof,
        "assigned_organ": "Finance",
        "supporting_organs": "Logistics, Telesales",
        "state": "ACTIVE RECOVERY",
        "opened_at": now_datetime(),
    })
    case.insert(ignore_permissions=True)
    from vitalvida.domain.orders import transition
    transition(order_name, "Payment Recovery")
    frappe.db.commit()
    _alert_finance(order_name, "PaymentRecoveryOpened")


def close_recovery_recovered(order_name, method="Moniepoint"):
    """Called from reconciliation when a late webhook confirms payment."""
    cases = frappe.get_all("Recovery Case", filters={"order": order_name, "state": ["in", ["OPEN", "ACTIVE RECOVERY"]]}, fields=["name"])
    for c in cases:
        frappe.db.set_value("Recovery Case", c.name, {"state": "RECOVERED", "recovery_method": method, "closed_at": now_datetime()})
    if cases:
        frappe.db.commit()


def mark_recovery_exhausted(case_name, cause=""):
    """Finance-only. Ends recovery and opens an Investigation Case. Never marks the order Paid."""
    roles = set(frappe.get_roles(frappe.session.user))
    if not (roles & FINANCE_ROLES):
        frappe.throw("Only Finance can declare a recovery exhausted.", frappe.PermissionError)
    case = frappe.get_doc("Recovery Case", case_name)
    if case.state in ("RECOVERED", "RECOVERY EXHAUSTED"):
        return {"success": False, "error": "Case already " + case.state}
    case.db_set("state", "RECOVERY EXHAUSTED")
    case.db_set("closed_at", now_datetime())
    inv = frappe.get_doc({
        "doctype": "Investigation Case",
        "opened_from": case_name,
        "order": case.order,
        "resolution": "Open",
        "opened_at": now_datetime(),
        "cause": cause or "",
    })
    inv.insert(ignore_permissions=True)
    if case.order:
        from vitalvida.domain.orders import transition
        transition(case.order, "Payment Investigation")
    frappe.db.commit()
    return {"success": True, "investigation_case": inv.name}


def _alert_finance(order_name, event):
    try:
        from vitalvida.notifications import send_notification
        order = frappe.get_doc("VV Order", order_name)
        send_notification(order, event=event, recipient_type="Owner", sender_channel="Transactional")
    except Exception:
        pass


def open_inventory_recovery_case(consignment_name, delivery_agent, shortfall_qty, shortfall_value=0, detail=""):
    """
    Loop 2.4 - open an INVENTORY Recovery Case for a custody discrepancy.

    Mirrors open_recovery_case (the cash opener) but for inventory custody: a
    shortfall detected at a custody handoff (consignment arrival short, or a
    Logistics-acceptance count mismatch) becomes a formal, tracked Recovery Case
    instead of a silent alert (Law 5 - no silent loss).

    Idempotent (Law 22): keyed on source_consignment. If an OPEN / ACTIVE RECOVERY
    inventory case already exists for this consignment, do nothing - one custody
    discrepancy yields at most one open inventory Recovery Case.
    """
    existing = frappe.db.exists("Recovery Case", {
        "source_consignment": consignment_name,
        "asset_type": "Inventory",
        "state": ["in", ["OPEN", "ACTIVE RECOVERY"]],
    })
    if existing:
        return existing

    case = frappe.get_doc({
        "doctype": "Recovery Case",
        "asset_type": "Inventory",
        "source_consignment": consignment_name,
        "amount": shortfall_value or 0,
        "last_known_custodian": delivery_agent,
        "expected_custodian": "Company",
        "assigned_organ": "Finance",
        "supporting_organs": "Logistics, Inventory",
        "recovery_method": (("Inventory shortfall: " + detail) if detail else "Inventory shortfall"),
        "state": "ACTIVE RECOVERY",
        "opened_at": now_datetime(),
    })
    case.insert(ignore_permissions=True)
    frappe.db.commit()

    try:
        frappe.publish_realtime(
            "inventory_recovery_opened",
            {"consignment": consignment_name, "da": delivery_agent, "qty": shortfall_qty, "case": case.name},
        )
    except Exception:
        pass

    return case.name
