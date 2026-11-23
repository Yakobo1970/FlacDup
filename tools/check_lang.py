"""Prueft, ob jeder Text in T("…") eine englische und franzoesische Uebersetzung in lang_table.py hat.

python tools/check_lang.py          -> Liste fehlender/ueberzaehliger Eintraege, Ende "0 fehlen" = alles da
python tools/check_lang.py --dump   -> fehlende Texte als Python-Zeilen zum Einfuegen ausgeben
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def texts() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for f in sorted(ROOT.rglob("*.py")):
        if any(part in ("build", "dist", "tools", "tests") for part in f.relative_to(ROOT).parts):
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "T" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                found.setdefault(node.args[0].value, []).append(f"{f.relative_to(ROOT)}:{node.lineno}")
    return found


def main():
    from lang_table import TABLE
    found = texts()
    missing = [t for t in found if t not in TABLE or not all(TABLE[t])]
    unused = [t for t in TABLE if t not in found]
    bad = [t for t in found if t in TABLE and any(
        sorted(set(_fields(x))) != sorted(set(_fields(t))) for x in TABLE[t] if x)]
    if "--dump" in sys.argv:
        for t in missing:
            print(f"    {t!r}: ({TABLE.get(t, ('', ''))[0]!r}, {TABLE.get(t, ('', ''))[1]!r}),")
        return
    for t in missing:
        print("FEHLT     ", repr(t), found[t][0])
    for t in bad:
        print("PLATZHALTER", repr(t))
    for t in unused:
        print("UNBENUTZT ", repr(t))
    print(f"{len(found)} Texte, {len(missing)} fehlen, {len(bad)} mit falschen Platzhaltern, {len(unused)} unbenutzt")
    sys.exit(1 if missing or bad else 0)


def _fields(s: str) -> list[str]:
    import string
    return [f for _, f, _, _ in string.Formatter().parse(s) if f]


if __name__ == "__main__":
    main()
