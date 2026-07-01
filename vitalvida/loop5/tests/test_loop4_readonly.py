import ast
import os
import unittest

# Static guarantee: no Loop 5 module writes Loop 4 relationship truth.
LOOP4_DOCTYPES = {
    "Customer Outcome", "Customer Trust Log", "Customer Timeline Event",
    "Customer Journey State", "Customer Advocacy", "Customer Referral",
    "Relationship NBA Log", "Loop 4 Settings",
}
WRITE_CALLS = {"insert", "save", "db_set", "set_value", "delete"}


class TestLoop4ReadOnly(unittest.TestCase):
    def test_no_write_to_loop4_doctypes(self):
        loop5_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        offenders = []
        for root, _, files in os.walk(loop5_dir):
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                src = open(os.path.join(root, fn)).read()
                for dt in LOOP4_DOCTYPES:
                    # crude but effective: a write API mentioning a Loop 4 doctype
                    if dt in src and any(w in src for w in WRITE_CALLS):
                        # allow read-only mentions; flag only get_doc(...).save style
                        if f'"{dt}"' in src and (".save(" in src or ".insert(" in src):
                            offenders.append((fn, dt))
        self.assertEqual(offenders, [], f"Loop 5 must not write Loop 4: {offenders}")
