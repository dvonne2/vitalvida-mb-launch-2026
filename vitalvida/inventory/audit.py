from __future__ import annotations
import ast
from pathlib import Path

FORBIDDEN_FIELDS={"current_stock","stock_quantity","balance_before","balance_after"}
FORBIDDEN_DOCTYPES={"DA Stock Entry","DA Warehouse"}
ALLOW={"vitalvida/inventory/authority.py","vitalvida/inventory/audit.py"}

def scan(root: str):
    rootp=Path(root); hits=[]
    for p in rootp.rglob("*.py"):
        rel=str(p.relative_to(rootp))
        if rel in ALLOW or "/tests/" in f"/{rel}": continue
        try: tree=ast.parse(p.read_text(errors="replace"), filename=rel)
        except SyntaxError as e: hits.append((rel,e.lineno,"SYNTAX",str(e))); continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Call):
                txt=ast.get_source_segment(p.read_text(errors="replace"),n) or ""
                if any(d in txt for d in FORBIDDEN_DOCTYPES) and any(k in txt for k in ["insert","set_value","db_set","sql","save"]):
                    hits.append((rel,n.lineno,"CUSTOM_LEDGER_WRITE",txt[:180]))
                if any(f in txt for f in FORBIDDEN_FIELDS) and any(k in txt for k in ["set_value","db_set","UPDATE","insert"]):
                    hits.append((rel,n.lineno,"CUSTOM_BALANCE_WRITE",txt[:180]))
            if isinstance(n, ast.Assign):
                for t in n.targets:
                    if isinstance(t,ast.Attribute) and t.attr in FORBIDDEN_FIELDS:
                        hits.append((rel,n.lineno,"CUSTOM_BALANCE_ASSIGN",t.attr))
    return hits
