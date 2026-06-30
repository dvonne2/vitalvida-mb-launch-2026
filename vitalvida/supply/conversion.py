"""
VitalVida Loop 3 — convert an APPROVED Supply Recommendation into action.

This is the ONLY bridge from Loop 3 planning to Loop 2 execution. It must never
create or credit DA stock. A "Send Stock to DA" recommendation becomes a
Consignment routed through the existing Loop 2 flow:

   can_hold_custody(da)  ->  Consignment (Pending)  ->  logistics_accept_consignment()
   ->  da_confirm_consignment()  ->  ledger credit (Loop 2, once)

A "Raise Stock Request" recommendation becomes a Stock Request (planning doc).
If custody is refused, we DO NOT proceed and we record a denied action.
"""
import frappe
from frappe.utils import nowdate, flt


CANONICAL_SOURCE_WAREHOUSE = "Finished Goods - VV"


def _default_source_warehouse():
    """
    Resolve the source warehouse deliberately (never "first warehouse"):
      1. a configured value in a 'Supply Settings' single doctype, if present;
      2. else the canonical Loop 3 source warehouse 'Finished Goods - VV'
         (recon-confirmed to exist), if it exists;
      3. else None — caller refuses rather than guesses.
    Loop 3 replenishment ships finished goods to DAs, so 'Finished Goods - VV' is
    the correct default. Other warehouses (Stores, Work In Progress, Goods In
    Transit) are deliberately NOT used as a replenishment source.
    """
    try:
        if frappe.db.exists("DocType", "Supply Settings"):
            configured = frappe.db.get_single_value("Supply Settings", "default_source_warehouse")
            if configured:
                return configured
    except Exception:
        pass
    if frappe.db.exists("Warehouse", CANONICAL_SOURCE_WAREHOUSE):
        return CANONICAL_SOURCE_WAREHOUSE
    return None


def _require_approved(rec_name):
    rec = frappe.get_doc("Supply Recommendation", rec_name)
    if rec.status not in ("Approved",):
        frappe.throw(f"Recommendation {rec_name} is '{rec.status}', not 'Approved'. "
                     f"Approve it before conversion.")
    return rec


def convert_to_stock_request(rec_name):
    """
    Idempotent: a recommendation already linked to a Stock Request returns it.
    """
    rec = _require_approved(rec_name)
    if rec.converted_to_stock_request:
        return frappe.get_doc("Stock Request", rec.converted_to_stock_request)

    sr = frappe.get_doc({
        "doctype": "Stock Request", "request_date": nowdate(),
        "source_recommendation": rec.name, "product": rec.product,
        "quantity": flt(rec.recommended_quantity), "reason": rec.reason or "",
        "urgency": "Emergency" if rec.recommendation_type == "Emergency Replenishment" else "Normal",
        "status": "Submitted", "requested_by": frappe.session.user,
    })
    sr.insert(ignore_permissions=True)
    rec.db_set("converted_to_stock_request", sr.name)
    rec.db_set("status", "Converted")
    frappe.db.commit()
    return sr


def convert_to_consignment(rec_name, source_warehouse=None):
    """
    Convert a 'Send Stock to DA' / 'Emergency Replenishment' recommendation into a
    Consignment through the Loop 2 flow. Custody is checked FIRST; refusal is logged
    and no consignment is created. Idempotent via converted_to_consignment.

    `source_warehouse` MUST be supplied by the approving Inventory Manager (the
    warehouse the stock ships from). Production has multiple warehouses
    (e.g. "Finished Goods - VV", "Stores - VV", "Goods In Transit - VV"), so the
    engine never guesses — choosing the wrong source warehouse would misstate where
    stock leaves from. If a configured default exists in Supply Settings it is used
    as a fallback; otherwise conversion is refused until a warehouse is named.

    NOTE: This creates the Consignment in 'Pending' status only. It does NOT accept
    it (Logistics does that) and does NOT confirm it (the DA does that, which is the
    sole stock-credit event). Loop 3 never credits stock.
    """
    rec = _require_approved(rec_name)
    if rec.converted_to_consignment:
        return frappe.get_doc("Consignment", rec.converted_to_consignment)

    from vitalvida.consignment import can_hold_custody
    da = rec.delivery_agent
    auth = can_hold_custody(da)
    if not auth.get("allowed"):
        # constitutional: log the denied attempt, refuse to convert
        try:
            from vitalvida.audit import record_denied_action
            record_denied_action("Custody", da,
                f"Supply recommendation {rec.name} could not convert to consignment: {auth.get('reason')}")
        except Exception:
            pass
        frappe.throw(f"Cannot create consignment for {da}: {auth.get('reason')}")

    # Resolve the source warehouse safely: explicit arg > configured default.
    # Never "first warehouse" — production has several and the choice is material.
    warehouse = source_warehouse or _default_source_warehouse()
    if not warehouse:
        frappe.throw(
            "No source warehouse specified. Pass source_warehouse (the warehouse the "
            "stock ships from, e.g. 'Finished Goods - VV') or configure a default in "
            "Supply Settings. Refusing to guess among multiple warehouses.")
    if not frappe.db.exists("Warehouse", warehouse):
        frappe.throw(f"Source warehouse '{warehouse}' does not exist.")
    qty = int(flt(rec.recommended_quantity))
    if qty <= 0:
        frappe.throw(f"Recommendation {rec.name} has no positive recommended quantity.")

    con = frappe.get_doc({
        "doctype": "Consignment",
        "consignment_id": f"SUP-{rec.name}",
        "from_location": warehouse,
        "to_location": da,
        "delivery_agent": da,
        "status": "Pending",
        "dispatch_date": nowdate(),
        "notes": f"Auto-created from Supply Recommendation {rec.name}. "
                 f"Routes through Loop 2: logistics accept -> DA confirm credits stock.",
        # Consignment Item child fields (recon-confirmed): product (Link Item),
        # qty_sent (reqd), qty_logistics_counted, qty_received. We set qty_sent;
        # logistics fills counted on acceptance, DA fills received on confirm.
        "items": [{"product": rec.product, "qty_sent": qty}],
    })
    con.insert(ignore_permissions=True)
    rec.db_set("converted_to_consignment", con.name)
    rec.db_set("status", "Converted")
    frappe.db.commit()
    return con
