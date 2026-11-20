#!/usr/bin/env python3
"""Find every translatable string (_("…") and N_("…")) and compare with locale/*.json.

usage:  python3 tools/i18n_check.py            report missing / unused per language
        python3 tools/i18n_check.py --keys     print all source strings as JSON
"""
import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "rpt_anywhere"


def keys() -> list[str]:
    found: list[str] = []
    for f in sorted(ROOT.rglob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id in ("_", "N_") and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                k = node.args[0].value
                if k not in found:
                    found.append(k)
    return found


def main() -> int:
    ks = keys()
    if "--keys" in sys.argv:
        print(json.dumps(ks, ensure_ascii=False, indent=1))
        return 0
    bad = 0
    for loc in sorted((ROOT / "locale").glob("*.json")):
        table = json.loads(loc.read_text(encoding="utf-8"))
        missing = [k for k in ks if k not in table]
        unused = [k for k in table if k not in ks]
        broken = [k for k in ks if k in table and
                  sorted(_fields(k)) != sorted(_fields(table[k]))]
        print(f"{loc.name}: {len(ks)} strings, {len(missing)} missing, {len(unused)} unused, "
              f"{len(broken)} with wrong placeholders")
        for k in missing[:50]:
            print("  MISSING:", k[:110])
        for k in broken:
            print("  PLACEHOLDER:", k[:110])
        bad += len(missing) + len(broken)
    return 1 if bad else 0


def _fields(s: str) -> list[str]:
    import string
    try:
        return [f for _t, f, _s, _c in string.Formatter().parse(s) if f is not None]
    except ValueError:
        return ["<invalid>"]


if __name__ == "__main__":
    sys.exit(main())
