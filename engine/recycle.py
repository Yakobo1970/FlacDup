"""Dateien aus dem Windows-Papierkorb zurueckholen (fuer Rueckgaengig).

Windows legt je geloeschter Datei im Ordner <Laufwerk>:\\$Recycle.Bin\\<SID>\\ zwei Dateien ab:
$Rxxxxxx.ext (der Inhalt) und $Ixxxxxx.ext (Kopf mit Originalpfad und Loeschzeit). Format des
$I-Kopfes ab Windows 10: Version (8 Byte, =2), Groesse (8), Loeschzeit als FILETIME (8),
Pfadlaenge in Zeichen (4), Pfad in UTF-16. Version 1 (Vista/7): Pfad fest 520 Byte ab Byte 24.
"""
from __future__ import annotations

import os
import struct


def _parse_info(path: str) -> tuple[str, int] | None:
    """(Originalpfad, Loeschzeit) aus einer $I-Datei."""
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        return None
    if len(data) < 24:
        return None
    version, _size, stamp = struct.unpack_from("<qqq", data, 0)
    if version == 2 and len(data) >= 28:
        n = struct.unpack_from("<i", data, 24)[0]
        raw = data[28:28 + 2 * n]
    elif version == 1:
        raw = data[24:24 + 520]
    else:
        return None
    return raw.decode("utf-16-le", "replace").split("\x00", 1)[0], stamp


def _bins(drive: str) -> list[str]:
    root = os.path.join(drive + os.sep, "$Recycle.Bin")
    try:
        names = os.listdir(root)
    except OSError:
        return []
    out = []
    for n in names:
        d = os.path.join(root, n)
        try:
            os.listdir(d)       # nur der eigene SID-Ordner ist lesbar
            out.append(d)
        except OSError:
            pass
    return out


def find(original: str) -> tuple[str, str] | None:
    """($R-Datei, $I-Datei) der zuletzt geloeschten Datei mit diesem Originalpfad."""
    original = os.path.normcase(os.path.abspath(original))
    drive = os.path.splitdrive(original)[0]
    best = None
    for b in _bins(drive):
        for name in os.listdir(b):
            if not name.startswith("$I"):
                continue
            info = _parse_info(os.path.join(b, name))
            if info is None or os.path.normcase(info[0]) != original:
                continue
            content = os.path.join(b, "$R" + name[2:])
            if os.path.exists(content) and (best is None or info[1] > best[0]):
                best = (info[1], content, os.path.join(b, name))
    return (best[1], best[2]) if best else None


def restore(original: str, target: str | None = None) -> bool:
    """Zuletzt geloeschte Datei mit diesem Originalpfad zurueckholen (wahlweise unter neuem Namen).
    False, wenn sie nicht mehr im Papierkorb liegt oder das Ziel schon existiert."""
    target = target or original
    if os.path.exists(target):
        return False
    hit = find(original)
    if hit is None:
        return False
    content, info = hit
    os.makedirs(os.path.dirname(target), exist_ok=True)
    os.replace(content, target)
    try:
        os.remove(info)
    except OSError:
        pass
    return True
