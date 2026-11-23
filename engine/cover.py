"""Coverbild einer Audiodatei: eingebettet (FLAC, MP3/ID3, M4A, OGG/Opus, WMA) oder als Bilddatei im Ordner."""
from __future__ import annotations

import base64
import os

FOLDER_NAMES = ("cover", "folder", "front", "albumart", "album")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".bmp", ".webp")


def embedded(path: str) -> bytes | None:
    try:
        from mutagen import File
        f = File(path)
    except Exception:
        return None
    if f is None:
        return None
    pics = []
    try:
        for p in getattr(f, "pictures", []) or []:                       # FLAC
            pics.append((p.type, p.data))
        tags = f.tags
        if tags is not None:
            if hasattr(tags, "getall"):                                    # ID3 (MP3, AIFF, WAV)
                for frame in tags.getall("APIC"):
                    pics.append((frame.type, frame.data))
            if "covr" in tags:                                             # MP4/M4A
                pics.extend((3, bytes(c)) for c in tags["covr"])
            if "metadata_block_picture" in tags:                           # OGG/Opus
                from mutagen.flac import Picture
                for b64 in tags["metadata_block_picture"]:
                    p = Picture(base64.b64decode(b64))
                    pics.append((p.type, p.data))
            if "WM/Picture" in tags:                                       # WMA: Typ, Laenge, MIME, Text, Daten
                for v in tags["WM/Picture"]:
                    raw = bytes(v.value)
                    i = raw.find(b"\xff\xd8")
                    if i < 0:
                        i = raw.find(b"\x89PNG")
                    if i >= 0:
                        pics.append((3, raw[i:]))
    except Exception:
        pass
    if not pics:
        return None
    front = [d for t, d in pics if t == 3]                                 # 3 = Vorderseite
    return (front or [d for _t, d in pics])[0]


def folder_image(path: str) -> str | None:
    folder = os.path.dirname(path)
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    imgs = [n for n in names if os.path.splitext(n)[1].lower() in IMAGE_EXT]
    for want in FOLDER_NAMES:
        for n in imgs:
            if os.path.splitext(n)[0].lower().startswith(want):
                return os.path.join(folder, n)
    if len(imgs) == 1:                     # einziges Bild im Albumordner
        return os.path.join(folder, imgs[0])
    return None


def cover_bytes(path: str) -> bytes | None:
    data = embedded(path)
    if data:
        return data
    img = folder_image(path)
    if img:
        try:
            with open(img, "rb") as fh:
                return fh.read()
        except OSError:
            return None
    return None
