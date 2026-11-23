"""Sprache der Oberflaeche (Deutsch, Englisch, Franzoesisch).

Im Code stehen die deutschen Texte in T("…", name=wert); die Uebersetzungen liegen in lang_table.py.
Platzhalter wie {n} werden nach der Uebersetzung per str.format gefuellt. Fehlt eine Uebersetzung,
erscheint der deutsche Text. Pruefen: python tools/check_lang.py (muss "0 fehlen" melden).

Die Sprache wird einmal beim Start festgelegt (ein Wechsel gilt nach einem Neustart):
Umgebungsvariable FLACDUP_LANG > config.json "lang" > Wahl im Installer (Registry) > Windows-Sprache.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

LANGS = {"de": "Deutsch", "en": "English", "fr": "Français"}
CONFIG_PATH = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "FlacDup" / "config.json"
REG_KEY = r"Software\Yakobo\FlacDup"


def _installer_choice() -> str | None:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            return str(winreg.QueryValueEx(k, "Language")[0]).lower()
    except Exception:
        return None


def _windows_language() -> str | None:
    try:
        import ctypes
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF   # primaere Sprach-ID
        return {0x07: "de", 0x09: "en", 0x0C: "fr"}.get(lid, "en")
    except Exception:
        return None


def _detect() -> str:
    env = os.environ.get("FLACDUP_LANG", "").lower()
    if env in LANGS:
        return env
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("lang")
        if cfg in LANGS:
            return cfg
    except Exception:
        pass
    for lang in (_installer_choice(), _windows_language()):
        if lang in LANGS:
            return lang
    return "de"


LANG = _detect()


def T(text: str, **kw) -> str:
    """Text in der aktuellen Sprache; {name}-Platzhalter werden mit kw gefuellt."""
    if LANG != "de":
        from lang_table import TABLE
        row = TABLE.get(text)
        if row:
            text = row[0 if LANG == "en" else 1] or text
    return text.format(**kw) if kw else text


def by_lang(de, en, fr):
    """Fuer laengere Texte (Hilfeseiten): je Sprache ein eigener Block."""
    return {"de": de, "en": en, "fr": fr}[LANG]


def set_language(lang: str):
    """Sprache fuer den naechsten Start merken."""
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    cfg["lang"] = lang
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
