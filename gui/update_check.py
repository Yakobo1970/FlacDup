"""Update-Pruefung: hoechstens einmal am Tag die neueste Version auf GitHub abfragen.

Einstellungen in config.json: update_check (an/aus, Standard an), update_last (Zeitpunkt der
letzten Abfrage), update_latest (zuletzt gefundene Version), update_skip (ausgeblendete Version).
Zum Testen ohne Netz: Umgebungsvariable FLACDUP_UPDATE_TEST=1.2.0 tut so, als gaebe es 1.2.0.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request

from PySide6.QtCore import QObject, Signal

from version import APP_NAME, VERSION

API_URL = "https://api.github.com/repos/Yakobo1970/FlacDup/releases/latest"
RELEASES_URL = "https://github.com/Yakobo1970/FlacDup/releases/latest"
INTERVAL_S = 24 * 3600


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:3]) + (0,) * (3 - min(3, len(nums)))


def is_newer(latest: str, current: str = VERSION) -> bool:
    return bool(latest) and parse_version(latest) > parse_version(current)


class UpdateChecker(QObject):
    found = Signal(str)          # neuere Version (ohne "v")

    def __init__(self, read_config, write_config, parent=None):
        super().__init__(parent)
        self._read, self._write = read_config, write_config

    def enabled(self) -> bool:
        return self._read().get("update_check", True)

    def set_enabled(self, on: bool):
        self._write({**self._read(), "update_check": bool(on)})

    def skip(self, version: str):
        self._write({**self._read(), "update_skip": version})

    def start(self, force: bool = False):
        """Im Hintergrund pruefen; meldet sich ueber found nur bei einer neueren, nicht ausgeblendeten Version."""
        test = os.environ.get("FLACDUP_UPDATE_TEST")
        cfg = self._read()
        if test:
            self._report(test, cfg)
            return
        if not force and not cfg.get("update_check", True):
            return
        if not force and time.time() - cfg.get("update_last", 0) < INTERVAL_S:
            self._report(cfg.get("update_latest", ""), cfg)   # bekanntes Ergebnis ohne neue Abfrage
            return
        threading.Thread(target=self._fetch, args=(force,), daemon=True).start()

    def _fetch(self, force: bool):
        try:
            req = urllib.request.Request(API_URL, headers={"User-Agent": f"{APP_NAME}/{VERSION}",
                                                           "Accept": "application/vnd.github+json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                tag = json.loads(r.read().decode("utf-8")).get("tag_name", "")
        except Exception:
            return
        latest = tag.lstrip("vV")
        cfg = {**self._read(), "update_last": time.time(), "update_latest": latest}
        self._write(cfg)
        self._report(latest, cfg if not force else {**cfg, "update_skip": ""})

    def _report(self, latest: str, cfg: dict):
        if is_newer(latest) and latest != cfg.get("update_skip"):
            self.found.emit(latest)
