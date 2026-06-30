"""
Loop 3 seed patch — idempotent.

Seeds the real production packages as Bundle Definitions so the decision engine
can resolve each sold VV Order package into the products (and unit counts) the DA
must physically hold. Also seeds MSS = 3 per product (Law 3/4 default).

Package → product map (from production storefront, 2026-06-29). Unit counts are
TOTAL units SHIPPED to the DA (paid + any "free" promo units), because every unit
shipped leaves the DA's custody and must therefore be stocked for fulfilment. The
"free" framing affects price only — and price here is a PRIORITISATION metric, not
booked revenue (see note on `bundle_price` below).

  Self Love Plus        ₦32,750   Shampoo 1, Conditioner 1, Pomade 1
  Self Love Return      ₦42,750   Pomade 3                       (returning customers; no shampoo/conditioner)
  Self Love B2GOF       ₦52,750   Shampoo 3, Pomade 3            (2 paid + 1 free each)
  Self Love Plus B2GOF  ₦66,750   Shampoo 3, Conditioner 3, Pomade 3
  Family Saves          ₦215,000  Shampoo 10, Conditioner 10, Pomade 10  (6 paid + 4 free each)

`bundle_price` is the current discounted selling price. Loop 3 uses it ONLY to
rank recommendations ("Potential Revenue Capacity" — where to send stock first).
It is NOT accounting revenue; actual revenue is recognised in Loop 1 after
Delivered + Paid. The engine never posts this figure to any ledger.

Idempotent: checks existence before creating; safe to run repeatedly.
"""
import frappe
from frappe.utils import nowdate

DEFAULT_MSS = 3

# product names must match the real Item master exactly: Shampoo, Conditioner, Pomade
PACKAGES = [
    {"name": "Self Love Plus",       "price": 32750.0,
     "reqs": {"Shampoo": 1, "Conditioner": 1, "Pomade": 1}},
    {"name": "Self Love Return",     "price": 42750.0,
     "reqs": {"Pomade": 3}},
    {"name": "Self Love B2GOF",      "price": 52750.0,
     "reqs": {"Shampoo": 3, "Pomade": 3}},
    {"name": "Self Love Plus B2GOF", "price": 66750.0,
     "reqs": {"Shampoo": 3, "Conditioner": 3, "Pomade": 3}},
    {"name": "Family Saves",         "price": 215000.0,
     "reqs": {"Shampoo": 10, "Conditioner": 10, "Pomade": 10}},
]


def execute():
    products = {d.name for d in frappe.get_all("Item", fields=["name"])}

    # 1. MSS rules (Law 3/4 default = 3 per product)
    for p in sorted(products):
        name = f"MSS-{p}"
        if not frappe.db.exists("Minimum Service Stock Rule", name):
            frappe.get_doc({
                "doctype": "Minimum Service Stock Rule", "product": p,
                "minimum_quantity": DEFAULT_MSS, "active": 1,
                "effective_from": nowdate(),
                "notes": "Seeded by loop3_seed_mss_and_bundle (Law 3/4 default).",
            }).insert(ignore_permissions=True)

    # 2. Bundle Definitions (the package -> product map). Only seed a package whose
    #    products all exist in the Item master; skip (and log) any that don't, rather
    #    than creating a bundle that references a missing product.
    for pkg in PACKAGES:
        missing = [p for p in pkg["reqs"] if p not in products]
        if missing:
            frappe.log_error(
                f"Loop 3 seed: package '{pkg['name']}' references missing products {missing}; skipped.",
                "Loop 3 Seed")
            continue
        if frappe.db.exists("Bundle Definition", pkg["name"]):
            continue
        doc = frappe.get_doc({"doctype": "Bundle Definition",
                              "bundle_name": pkg["name"], "bundle_price": pkg["price"],
                              "is_active": 1})
        for prod, qty in pkg["reqs"].items():
            doc.append("products", {"product": prod, "quantity_required": qty})
        doc.insert(ignore_permissions=True)

    frappe.db.commit()
