"""
M5 Seed Script — 29 Real Delivery Agents from VitalVida DA List
Extracted from warehouse CSV: DA - Name (State)

Run in bench console:
    exec(open('apps/vitalvida/vitalvida/vitalvida/doctype/delivery_agent/seed_agents.py').read())
"""

import frappe

AGENTS = [
    # Abia
    {"agent_name": "Uma Ota Samuel",         "phone": "2348100000004", "state": "Abia"},
    # Abuja (3 agents)
    {"agent_name": "Jacob John",             "phone": "2348100000001", "state": "Abuja"},
    {"agent_name": "Moses Makolo",           "phone": "2348100000002", "state": "Abuja"},
    {"agent_name": "Ugwunwa Chigozie",       "phone": "2348100000003", "state": "Abuja"},
    # Adamawa
    {"agent_name": "Isiaka",                 "phone": "2348100000006", "state": "Adamawa"},
    # Akwa Ibom
    {"agent_name": "Uko",                    "phone": "2348100000005", "state": "Akwa Ibom"},
    # Anambra
    {"agent_name": "Uche",                   "phone": "2348100000007", "state": "Anambra"},
    # Bayelsa
    {"agent_name": "Roberta",               "phone": "2348100000008", "state": "Bayelsa"},
    # Delta
    {"agent_name": "Hannaniah",             "phone": "2348100000009", "state": "Delta"},
    # Ebonyi
    {"agent_name": "Denis",                 "phone": "2348100000010", "state": "Ebonyi"},
    # Edo
    {"agent_name": "SamSucc",               "phone": "2348100000011", "state": "Edo"},
    # Ekiti
    {"agent_name": "Fasanya Adeola",        "phone": "2348100000012", "state": "Ekiti"},
    # Enugu
    {"agent_name": "Oha Valentine Chibuzo", "phone": "2348100000013", "state": "Enugu"},
    # Imo
    {"agent_name": "Favour",                "phone": "2348100000014", "state": "Imo"},
    # Kaduna
    {"agent_name": "Ekpa John",             "phone": "2348100000015", "state": "Kaduna"},
    # Kogi
    {"agent_name": "Moses Ehindero",        "phone": "2348100000016", "state": "Kogi"},
    # Kwara
    {"agent_name": "Seun Adewoye",          "phone": "2348100000017", "state": "Kwara"},
    # Lagos (3 agents)
    {"agent_name": "Franklin",              "phone": "2348100000018", "state": "Lagos"},
    {"agent_name": "Fadsup",                "phone": "2348100000019", "state": "Lagos"},
    {"agent_name": "Samson",                "phone": "2348100000020", "state": "Lagos"},
    # Nasarawa
    {"agent_name": "Adamu",                 "phone": "2348100000021", "state": "Nasarawa"},
    # Niger
    {"agent_name": "Moses",                 "phone": "2348100000022", "state": "Niger"},
    # Ondo
    {"agent_name": "Betiku",                "phone": "2348100000023", "state": "Ondo"},
    # Oyo (2 agents)
    {"agent_name": "Paul Alabi",            "phone": "2348100000024", "state": "Oyo"},
    {"agent_name": "Adigun Enitan",         "phone": "2348100000025", "state": "Oyo"},
    # Plateau
    {"agent_name": "Mayowa Samuel",         "phone": "2348100000026", "state": "Plateau"},
    # Rivers
    {"agent_name": "Ijeoma Geraldine",      "phone": "2348100000027", "state": "Rivers"},
    # Sokoto
    {"agent_name": "Dare Abayomi",          "phone": "2348100000028", "state": "Sokoto"},
    # Taraba
    {"agent_name": "Nafiu Haruna Sale",     "phone": "2348100000029", "state": "Taraba"},
]


def seed_delivery_agents():
    created = 0
    skipped = 0

    for da in AGENTS:
        if frappe.db.exists("Delivery Agent", da["agent_name"]):
            skipped += 1
            print(f"⏭  Skipped (exists): {da['agent_name']}")
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Delivery Agent",
                "agent_name": da["agent_name"],
                "phone": da["phone"],
                "state": da["state"],
                "active": 1,
                "total_orders": 0,
                "success_rate": 0
            })
            doc.insert(ignore_permissions=True)
            created += 1
            print(f"✅ Created: {da['agent_name']} ({da['state']})")
        except Exception as e:
            print(f"❌ Failed: {da['agent_name']} — {str(e)}")

    frappe.db.commit()
    print(f"\n{'='*50}")
    print(f"Seed complete: {created} created, {skipped} skipped")
    print(f"Total agents in DB: {frappe.db.count('Delivery Agent')}")


seed_delivery_agents()
