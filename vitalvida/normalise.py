import frappe


def normalise_payload(raw: dict, queue_row_name: str) -> dict:
    data = raw.copy()
    log = []

    # ── READ ACTIVE MAPPINGS FROM INBOUND FIELD MAP DOCTYPE ──
    try:
        mappings = frappe.get_all(
            "Inbound Field Map",
            filters={"active": 1},
            fields=["source_field", "target_field"]
        )
        for m in mappings:
            if m.source_field in raw and m.target_field not in data:
                data[m.target_field] = raw[m.source_field]
                log.append(f"Mapped: {m.source_field} → {m.target_field}")
    except Exception:
        log.append("Warning: Could not read Inbound Field Map")

    # ── PHONE ─────────────────────────────────────────────────
    phone = (
        raw.get("customerPhone")
        or raw.get("customer_phone")
        or raw.get("phone")
        or ""
    )
    phone = "".join(filter(str.isdigit, str(phone)))

    if phone.startswith("0") and len(phone) == 11:
        phone = "234" + phone[1:]
        log.append(f"Phone fixed: 0... → 234...")
    elif phone.startswith("234") and len(phone) == 13:
        log.append("Phone already correct")
    elif not phone:
        raise ValueError("Missing phone number — rejected")
    else:
        raise ValueError(f"Bad phone format: {phone}")

    data["customer_phone"] = phone

    # ── CUSTOMER NAME ─────────────────────────────────────────
    # Cover every field name variant from all sources
    data["customer_name"] = (
        raw.get("customer_name")       # ← React-Web / your current payload
        or raw.get("customerFullName")
        or raw.get("customerName")
        or raw.get("full_name")
        or raw.get("fullName")
        or raw.get("name")
        or ""
    )
    log.append(f"Name: {data['customer_name']}")

    # ── PACKAGE / PRODUCT ─────────────────────────────────────
    # Cover every field name variant from all sources
    package = (
        raw.get("package_name")        # standard ERPNext name
        or raw.get("packageName")      # camelCase variant
        or raw.get("product")          # ← React-Web / your current payload
        or raw.get("product_name")
        or raw.get("productName")
        or raw.get("item")
        or ""
    )

    # Resolve package aliases (website shortnames → exact VV Package names)
    PACKAGE_ALIASES = {
        "hair grow":          "Hair Growth Oil",
        "hair growth":        "Hair Growth Oil",
        "hair growth oil":    "Hair Growth Oil",
        "self love":          "Self Love Plus",
        "self love plus":     "Self Love Plus",
        "self love return":   "Self Love Return",
        "self love b2gof":    "Self Love B2GOF",
        "family saves":       "Family Saves",
        "self love plus b2gof": "Self Love Plus B2GOF",
    }
    if package:
        resolved = PACKAGE_ALIASES.get(package.lower().strip())
        if resolved:
            log.append(f"Package alias: '{package}' → '{resolved}'")
            package = resolved

    # Validate package exists in ERPNext — only if package is not empty
    if package and not frappe.db.exists("VV Package", package):
        # Don't hard reject — log warning and keep going
        # Telesales can correct the package on the confirmation call
        log.append(f"Warning: Package '{package}' not found in ERPNext — saved as-is")

    data["package_name"] = package
    log.append(f"Package: {package}")

    # ── AMOUNT ────────────────────────────────────────────────
    data["total"] = float(
        raw.get("totalAmount")
        or raw.get("total_amount")
        or raw.get("packageAmount")
        or raw.get("productPrice")
        or raw.get("total_payable")
        or raw.get("amount")
        or raw.get("price")
        or 0
    )
    log.append(f"Total: {data['total']}")

    # ── DELIVERY FEE ──────────────────────────────────────────
    delivery_type = (
        raw.get("deliveryType")
        or raw.get("delivery_type")
        or "STANDARD"
    ).upper()

    try:
        fee_config = frappe.get_single("Delivery Fee Config")
        same_day = fee_config.same_day_fee or 5000
        standard = fee_config.standard_fee or 3000
    except Exception:
        same_day = 5000
        standard = 3000

    data["delivery_fee"] = same_day if delivery_type == "SAME_DAY" else standard
    data["delivery_type"] = delivery_type
    log.append(f"Delivery fee: {data['delivery_fee']} ({delivery_type})")

    # ── LOCATION FIELDS ───────────────────────────────────────
    # These come straight from payload — just ensure they exist
    data["state"]    = raw.get("state", "")
    data["lga"]      = raw.get("lga", "")
    data["address"]  = raw.get("address", "")
    data["landmark"] = raw.get("landmark", "")  # blank if not sent — telesales fills on call
    log.append(f"Location: {data['state']} / {data['lga']}")

    # ── PARTIAL FLAG ──────────────────────────────────────────
    # Mark as partial if name or package is missing
    if not data["customer_name"] or not data["package_name"]:
        data["is_partial"] = 1
        log.append("Flagged as Partial — missing name or package")
    else:
        data["is_partial"] = 0

    # ── SAVE NORMALISATION LOG TO QUEUE ROW ───────────────────
    frappe.db.set_value(
        "Vitalvida Webhook Queue",
        queue_row_name,
        "normalisation_log",
        "\n".join(log)
    )

    return data
