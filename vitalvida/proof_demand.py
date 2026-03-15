"""
M15 — DA Proof Demand Expiry Checker
proof_demand.py

check_expired_proof_demands() runs every hour via cron: 0 * * * *
Finds all Pending Proof Demands past their deadline and:
  1. Sets status = Expired
  2. Adds 1 strike (Proof Demand Failure)
  3. Alerts Operations
"""

import frappe
from frappe.utils import now_datetime


def check_expired_proof_demands() -> None:
    """Runs hourly. Expires overdue proof demands and adds strikes."""
    from vitalvida.consignment_strike import add_strike
    from vitalvida.notifications import send_notification

    now = now_datetime()

    expired = frappe.db.sql("""
        SELECT name, delivery_agent, proof_type, deadline
        FROM `tabDA Proof Demand`
        WHERE status = 'Pending'
        AND deadline < %s
    """, (now,), as_dict=True)

    for demand in expired:
        try:
            frappe.db.set_value("DA Proof Demand", demand.name, "status", "Expired")
            frappe.db.commit()

            add_strike(
                delivery_agent=demand.delivery_agent,
                source="Proof Demand Failure",
                reason=f"Proof demand '{demand.proof_type}' expired without submission "
                       f"(deadline: {demand.deadline})"
            )

            # T31 fix: freeze all DA warehouses on proof demand expiry
            try:
                from vitalvida.freeze import freeze_da_warehouse
                warehouses = frappe.get_all(
                    "DA Warehouse",
                    filters={"delivery_agent": demand.delivery_agent},
                    fields=["name", "product"]
                )
                for wh in warehouses:
                    freeze_da_warehouse(
                        demand.delivery_agent,
                        wh.product,
                        reason=f"Proof demand expired: {demand.proof_type} (deadline: {demand.deadline})"
                    )
            except Exception as fe:
                frappe.log_error(
                    f"M15: Freeze failed after proof demand expiry for DA={demand.delivery_agent}: {str(fe)}",
                    "M15 Proof Demand Freeze Error"
                )

            da_name = (
                frappe.db.get_value("Delivery Agent", demand.delivery_agent, "agent_name")
                or demand.delivery_agent
            )

            stub = frappe._dict({
                "name": demand.name,
                "customer_name": da_name,
                "customer_phone": "",
                "delivery_agent_name": da_name,
                "proof_type": demand.proof_type,
                "total_payable": 0,
                "package_contents": "",
                "address": "",
            })

            send_notification(
                stub,
                event="ProofDemandExpired",
                recipient_type="Owner",
                sender_channel="Transactional"
            )

        except Exception as e:
            frappe.log_error(
                f"M15: Proof demand expiry failed for {demand.name}: {str(e)}",
                "M15 Proof Demand Error"
            )
