"""Band, Titel, Album, Jahr, Tracknummer aus den Tags -- mit Rueckfall auf Datei- und
Ordnernamen nach dem Muster des Archivs ("NN - Band - Titel.mp3" in "Band [Jahr] Album")."""
from __future__ import annotations

import os
import re

_FILE_RE = re.compile(r"^\s*(\d{1,3})\s*-\s*(.+?)\s+-\s+(.+)$")
_DIR_RE = re.compile(r"^(.+?)\s*\[(\d{4})\]\s*(.*)$")


def _first(v):
    if isinstance(v, list):
        v = v[0] if v else None
    return str(v).strip() if v is not None and str(v).strip() else None


def read_tags(path: str) -> dict:
    tags = {}
    try:
        import mutagen
        f = mutagen.File(path, easy=True)
        if f is not None and f.tags is not None:
            for key, name in (("artist", "artist"), ("albumartist", "albumartist"), ("title", "title"),
                              ("album", "album"), ("date", "year"), ("tracknumber", "track")):
                val = _first(f.tags.get(key))
                if val:
                    tags[name] = val
    except Exception:
        pass
    if not tags.get("artist") and tags.get("albumartist"):
        tags["artist"] = tags["albumartist"]
    tags.pop("albumartist", None)
    if tags.get("year"):
        tags["year"] = tags["year"][:4]
    if tags.get("track"):
        tags["track"] = tags["track"].split("/")[0]
    # Rueckfall auf die Namen
    stem = os.path.splitext(os.path.basename(path))[0]
    m = _FILE_RE.match(stem)
    if m:
        tags.setdefault("track", m.group(1))
        tags.setdefault("artist", m.group(2))
        tags.setdefault("title", m.group(3))
    else:
        tags.setdefault("title", stem)
    d = _DIR_RE.match(os.path.basename(os.path.dirname(path)))
    if d:
        tags.setdefault("artist", d.group(1))
        tags.setdefault("year", d.group(2))
        if d.group(3):
            tags.setdefault("album", d.group(3))
    return tags
