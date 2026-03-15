import frappe

def normalise_payload(raw: dict, queue_row_name: str) -> dict:
    data = raw.copy()
    log = []

    #READ ACTIVE MAPPINGS FROM INBOUND FIELD MAP DOCTYPE
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

    #Fix phone number
    phone = (
        raw.get("customerPhone")
        or raw.get("phone")
        or raw.get("customer_phone")
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

    #Fix customer name
    data["customer_name"] = (
        raw.get("customerFullName")
        or raw.get("customerName")
        or raw.get("name")
        or ""
    )
    log.append(f"Name: {data['customer_name']}")

    #Fix amount
    data["total"] = float(
        raw.get("totalAmount")
        or raw.get("packageAmount")
        or raw.get("productPrice")
        or raw.get("amount")
        or raw.get("price")
        or 0
    )
    log.append(f"Total: {data['total']}")

    #Check package exists in ERPNext
    package = (
        raw.get("package_name")
        or raw.get("packageName")
        or ""
    )
    if package and not frappe.db.exists("Package", package):
        raise ValueError(f"Unknown package: '{package}' — rejected")
    data["package_name"] = package
    log.append(f"Package: {package}")

    #Calculate delivery fee
    delivery_type = (
        raw.get("deliveryType")
        or raw.get("delivery_type")
        or "STANDARD"
    )
    try:
        fee_config = frappe.get_single("Delivery Fee Config")
        same_day = fee_config.same_day_fee or 5000
        standard = fee_config.standard_fee or 3000
    except Exception:
        same_day = 5000
        standard = 3000

    data["delivery_fee"] = same_day if delivery_type == "SAME_DAY" else standard
    data["delivery_type"] = delivery_type
    log.append(f"Delivery fee: {data['delivery_fee']}")

    #Flag incomplete orders
    if not data["customer_name"] and not package:
        data["is_partial"] = 1
        log.append("Flagged as Partial")
    else:
        data["is_partial"] = 0

    #Save log to queue row
    frappe.db.set_value(
        "Vitalvida Webhook Queue",
        queue_row_name,
        "normalisation_log",
        "\n".join(log)
    )

    return data