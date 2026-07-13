import frappe
from frappe.utils import cint, now_datetime

# Package 02 (Constitution PRD-005): component quantities come from the structured
# recipe (Bundle Definition Item), never from a parsed display string.
from vitalvida.recipe import classify

# ============================================================================
# KNOWN LIMITATION — NOT FIXED BY PACKAGE 02 (owned by the Inventory package)
# ----------------------------------------------------------------------------
# The stock-movement mechanism below is a CUSTOM per-component `DA Stock Entry`
# writer with a whole-order idempotency guard and a per-component commit. That
# design has a pre-existing partial-deduction hazard:
#     component 1 commits -> component 2 fails -> a retry sees the order already
#     has a Deduction and skips the rest -> order left permanently half-deducted.
#
# Package 02 is a PRODUCTS-domain change (PRD-005: resolve components from the
# structured recipe instead of parsing display text). It deliberately does NOT
# alter the transaction/commit/idempotency mechanics, because the constitutional
# replacement of this entire path is an INVENTORY-domain change:
#     INV-004 (Sprint S0): "Deduct DA stock only when Delivered + Payment
#     Confirmed" -> "Deduct via Delivery Note from DA WH", with SLE as the
#     balance authority (INV-006) and negative-stock blocking (INV-008).
# Until the Inventory package lands INV-004, this path is NOT considered safe or
# constitutionally complete. Package 02 only changes HOW components are resolved,
# and preserves existing behaviour for everything else (dry-run gated).
# ============================================================================


def deduct_on_payment(order_name):
    # Package 03 cutover router. Transition preserves Package 02 legacy behavior.
    from vitalvida.inventory.authority import is_live
    if is_live():
        try:
            from vitalvida.inventory.movements import delivery_note_for_order
            return delivery_note_for_order(order_name)
        except Exception as exc:
            frappe.log_error(f"Package 03 inventory consequence failed for {order_name}: {exc}", "INV-004 Delivery Note Error")
            return
    return _legacy_deduct_on_payment(order_name)

def _legacy_deduct_on_payment(order_name):
    """
    Deduct DA stock for a paid order.

    Package 02 change (PRD-005): resolve components from the structured recipe.
      - exactly one active Bundle Definition matches  -> structured components;
      - none match                                    -> legacy display-string
                                                         fallback (transition only);
      - more than one matches (ambiguous)             -> STOP, do not deduct,
                                                         log; never guess.

    NOTE: transaction/idempotency mechanics are unchanged from baseline and carry
    the partial-deduction limitation documented at module top (INV-004 owns the
    fix). This function is not a full/safe deduction authority.
    """
    try:
        # 0. Whole-order idempotency guard (unchanged; see KNOWN LIMITATION).
        already_deducted = frappe.db.exists("DA Stock Entry", {
            "reference_order": order_name,
            "entry_type": "Deduction",
        })
        if already_deducted:
            return

        order = frappe.get_doc("VV Order", order_name)

        if not order.delivery_agent:
            frappe.log_error(
                f"M13: Order {order_name} has no delivery_agent — deduction skipped.",
                "M13 Deduction Error")
            return

        if not order.package_name:
            frappe.log_error(
                f"M13: Order {order_name} has no package_name — deduction skipped.",
                "M13 Deduction Error")
            return

        # 1. Resolve components (structured / fallback / ambiguous-stop).
        status, payload = classify(order.package_name)

        if status == "ambiguous":
            frappe.log_error(
                f"M13/PRD-005: package '{order.package_name}' matches multiple active "
                f"Bundle Definitions {payload} on order {order_name}; deduction "
                f"STOPPED (fail-closed, no guess).",
                "M13 Deduction Error")
            return

        if status == "structured":
            components = [(p, q) for (p, q) in payload if q > 0]
            resolution_path = "structured"
        else:  # "empty" -> legacy display-string fallback (transition only)
            resolution_path = "legacy-string"
            contents = _read_legacy_contents(order.package_name)
            if not contents:
                frappe.log_error(
                    f"M13: Package {order.package_name} has no active Bundle "
                    f"Definition and no legacy contents — deduction skipped for "
                    f"order {order_name}.",
                    "M13 Deduction Error")
                return
            components = [(p, q) for (p, q) in _parse_contents(contents) if q > 0]
            if not components:
                frappe.log_error(
                    f"M13: Could not parse legacy contents '{contents}' for package "
                    f"{order.package_name} on order {order_name}.",
                    "M13 Deduction Error")
                return

        try:
            frappe.logger("vitalvida.deduction").info(
                f"order={order_name} package={order.package_name} "
                f"path={resolution_path} components={components}")
        except Exception:
            pass

        # 2. Write one DA Stock Entry per component.
        #    (Transaction mechanics unchanged — see KNOWN LIMITATION / INV-004.)
        for product, qty in components:
            _deduct_da_stock(
                delivery_agent=order.delivery_agent,
                product=product,
                quantity=qty,
                order=order_name,
            )

    except Exception as e:
        # Payment confirmation must never be blocked.
        frappe.log_error(
            f"M13 deduction failed for order {order_name}: {str(e)}",
            "M13 Deduction Error")


def _read_legacy_contents(package_name):
    """Transition fallback ONLY: read the legacy display string from VV Package,
    then Package, then a single `item` field. Returns "" if nothing found.
    Removed once Bundle Definition coverage of live package_name values is
    confirmed (PRD-005 completion)."""
    for dt in ["VV Package", "Package"]:
        try:
            contents = frappe.db.get_value(dt, package_name, "contents") or ""
            if contents:
                return contents
        except Exception:
            continue
    for dt in ["VV Package", "Package"]:
        try:
            product = frappe.db.get_value(dt, package_name, "item")
            if product:
                return f"1 {product}"
        except Exception:
            continue
    return ""


def _parse_contents(contents):
    """LEGACY / FALLBACK PARSER (transition only; not called by new code paths).
    Parses "1 Shampoo · 1 Pomade" -> [("Shampoo",1),("Pomade",1)]."""
    items = []
    if not contents:
        return items
    for part in contents.split("\u00b7"):
        part = part.strip()
        if not part:
            continue
        tokens = part.split()
        if tokens and tokens[0].isdigit():
            qty = int(tokens[0])
            product = " ".join(tokens[1:])
        else:
            qty = 1
            product = part
        if product:
            items.append((product.strip(), qty))
    return items


def _deduct_da_stock(delivery_agent, product, quantity, order):
    """
    Create a DA Stock Entry (Deduction). DA Stock Entry.after_insert updates the
    warehouse balance. Per-component commit + whole-order idempotency guard are
    UNCHANGED from baseline (see KNOWN LIMITATION at module top; INV-004 replaces
    this with a Delivery Note). Do not manually update DA Warehouse here.
    """
    now = now_datetime()
    try:
        entry = frappe.get_doc({
            "doctype": "DA Stock Entry",
            "delivery_agent": delivery_agent,
            "product": product,
            "entry_type": "Deduction",
            "direction": "Out",
            "quantity": quantity,
            "reference_order": order,
            "creation": now,
        })
        entry.insert(ignore_permissions=True)
        frappe.db.commit()
    except frappe.DuplicateEntryError:
        pass
    except Exception as e:
        frappe.log_error(
            f"M13: DA Stock Entry failed for DA={delivery_agent}, "
            f"product={product}, order={order}: {str(e)}",
            "M13 Stock Entry Error")
