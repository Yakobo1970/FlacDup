"""Vollstaendige Tags fuer den Tag-Abgleich A/B: einfache Felder, Komponist, Kommentar, Liedtext und Cover.

Unterstuetzt ID3 (MP3, WAV, AIFF), Vorbis-Kommentare (FLAC, OGG, Opus), MP4 (M4A, M4B, MP4) und
ASF (WMA, nur Felder -- kein Cover schreiben). Geschrieben werden nur geaenderte Felder, damit
Mehrfachwerte und alle anderen Tags unangetastet bleiben.

Rueckgaengig: ``snapshot`` haelt das komplette Tag-Objekt der Datei im Speicher, ``restore`` schreibt
es unveraendert zurueck (der Klang wird dabei nicht angefasst).
"""
from __future__ import annotations

import base64

from lang import T

from .cover import embedded, folder_image

FIELDS = [("title", T("Titel")), ("artist", T("Interpret")), ("album", T("Album")),
          ("albumartist", T("Album-Interpret")), ("tracknumber", T("Track")), ("discnumber", T("CD")),
          ("date", T("Jahr")), ("origyear", T("Original-Jahr")), ("genre", T("Genre")), ("composer", T("Komponist")), ("comment", T("Kommentar")),
          ("lyrics", T("Liedtext"))]
KEYS = [k for k, _ in FIELDS]

KEEP = object()          # Cover unveraendert lassen

_ID3 = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2", "tracknumber": "TRCK",
        "discnumber": "TPOS", "date": "TDRC", "genre": "TCON", "composer": "TCOM"}
_VORBIS = {"title": "title", "artist": "artist", "album": "album", "albumartist": "albumartist",
           "tracknumber": "tracknumber", "discnumber": "discnumber", "date": "date", "genre": "genre",
           "composer": "composer", "comment": "comment", "lyrics": "lyrics"}
_MP4 = {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "albumartist": "aART", "date": "\xa9day",
        "genre": "\xa9gen", "composer": "\xa9wrt", "comment": "\xa9cmt", "lyrics": "\xa9lyr"}
_ASF = {"title": "Title", "artist": "Author", "album": "WM/AlbumTitle", "albumartist": "WM/AlbumArtist",
        "tracknumber": "WM/TrackNumber", "discnumber": "WM/PartOfSet", "date": "WM/Year", "genre": "WM/Genre",
        "composer": "WM/Composer", "comment": "Description", "lyrics": "WM/Lyrics"}


# Original-Jahr steht je nach Programm unter verschiedenen Schluesseln. Gelesen wird der erste vorhandene,
# geschrieben werden alle vorhandenen (damit sie sich nicht widersprechen); fehlen alle, der erste der Liste.
# Schluessel mit "year" bekommen nur die Jahreszahl.
_ORIG_VORBIS = ["original date", "originaldate", "originalyear", "origyear", "original year"]
_ORIG_TXXX = ("originalyear", "original year", "originaldate", "original date")
_ORIG_MP4 = ["----:com.apple.iTunes:ORIGINALDATE", "----:com.apple.iTunes:ORIGINALYEAR"]
_ORIG_ASF = ["WM/OriginalReleaseYear", "WM/OriginalReleaseTime"]


def _year_only(key: str, v: str) -> str:
    return v[:4] if "year" in key.lower() and v[:4].isdigit() else v


def _read_orig(tags, kind: str) -> str:
    if kind == "id3":
        fr = tags.get("TDOR") or tags.get("TORY")
        if fr is not None and _join(fr.text):
            return _join(fr.text)
        for fr in tags.getall("TXXX"):
            if fr.desc.lower() in _ORIG_TXXX and _join(fr.text):
                return _join(fr.text)
    elif kind == "vorbis":
        for key in _ORIG_VORBIS:
            if tags.get(key):
                return _join(tags.get(key))
    elif kind == "mp4":
        for key in _ORIG_MP4:
            if key in tags and tags[key]:
                return _join(bytes(x).decode("utf-8", "replace") for x in tags[key])
    elif kind == "asf":
        for key in _ORIG_ASF:
            if key in tags:
                return _join(str(x) for x in tags[key])
    return ""


def _write_orig(tags, kind: str, v: str):
    if kind == "id3":
        from mutagen import id3
        txxx = [fr for fr in tags.getall("TXXX") if fr.desc.lower() in _ORIG_TXXX]
        tags.delall("TDOR")
        tags.delall("TORY")
        if v:
            tags.add(id3.TDOR(encoding=3, text=[v]))
        for fr in txxx:
            del tags[fr.HashKey]
            if v:
                tags.add(id3.TXXX(encoding=3, desc=fr.desc, text=[_year_only(fr.desc, v)]))
        return
    if kind == "vorbis":
        keys = [k for k in _ORIG_VORBIS if k in tags] or _ORIG_VORBIS[:1]
        for key in keys:
            if v:
                tags[key] = [_year_only(key, v)]
            elif key in tags:
                del tags[key]
        return
    if kind == "mp4":
        from mutagen.mp4 import MP4FreeForm
        keys = [k for k in _ORIG_MP4 if k in tags] or _ORIG_MP4[:1]
        for key in keys:
            if v:
                tags[key] = [MP4FreeForm(_year_only(key, v).encode("utf-8"))]
            elif key in tags:
                del tags[key]
        return
    if kind == "asf":
        keys = [k for k in _ORIG_ASF if k in tags] or _ORIG_ASF[:1]
        for key in keys:
            if v:
                tags[key] = [_year_only(key, v)]
            elif key in tags:
                del tags[key]


class TagError(Exception):
    pass


def _kind(tags) -> str | None:
    from mutagen._vorbis import VComment
    from mutagen.asf import ASFTags
    from mutagen.id3 import ID3
    from mutagen.mp4 import MP4Tags
    if isinstance(tags, ID3):
        return "id3"
    if isinstance(tags, VComment):
        return "vorbis"
    if isinstance(tags, MP4Tags):
        return "mp4"
    if isinstance(tags, ASFTags):
        return "asf"
    return None


def _open(path: str):
    from mutagen import File
    try:
        f = File(path)
    except Exception as exc:
        raise TagError(T("Tags nicht lesbar: {err}", err=exc)) from exc
    if f is None:
        raise TagError(T("Tags dieses Formats können nicht geschrieben werden"))
    return f


def _file_kind(f) -> str | None:
    """Tag-Art auch fuer Dateien ohne Tags (nach Dateityp)."""
    if f.tags is not None:
        return _kind(f.tags)
    name = type(f).__name__
    if name in ("MP3", "WAVE", "AIFF", "EasyMP3"):
        return "id3"
    if name in ("FLAC", "OggVorbis", "OggOpus", "OggFLAC", "OggSpeex"):
        return "vorbis"
    if name in ("MP4", "EasyMP4"):
        return "mp4"
    if name == "ASF":
        return "asf"
    return None


def _join(vals) -> str:
    return "; ".join(str(v).strip() for v in vals if str(v).strip())


# ---------------------------------------------------------------- Lesen
def read(path: str) -> dict:
    """{'values': {feld: text}, 'cover': bytes|None, 'cover_folder': bool, 'writable': bool,
    'cover_writable': bool}"""
    f = _open(path)
    kind = _file_kind(f)
    tags = f.tags
    vals = {k: "" for k in KEYS}
    if tags is not None:
        try:
            if kind == "id3":
                for k, fid in _ID3.items():
                    fr = tags.get(fid)
                    if fr is not None:
                        vals[k] = _join(fr.genres if fid == "TCON" else fr.text)
                comm = [c for c in tags.getall("COMM") if not c.desc]
                if comm:
                    vals["comment"] = _join(comm[0].text)
                uslt = tags.getall("USLT")
                if uslt:
                    vals["lyrics"] = str(uslt[0].text)
            elif kind == "vorbis":
                for k, key in _VORBIS.items():
                    v = tags.get(key)
                    if not v and k == "lyrics":
                        v = tags.get("unsyncedlyrics")
                    if not v and k == "comment":
                        v = tags.get("description")
                    if v:
                        vals[k] = _join(v) if k != "lyrics" else str(v[0])
            elif kind == "mp4":
                for k, key in _MP4.items():
                    if key in tags:
                        vals[k] = _join(tags[key]) if k != "lyrics" else str(tags[key][0])
                for k, key in (("tracknumber", "trkn"), ("discnumber", "disk")):
                    if key in tags and tags[key]:
                        n, tot = (tuple(tags[key][0]) + (0, 0))[:2]
                        vals[k] = f"{n}/{tot}" if tot else (str(n) if n else "")
            elif kind == "asf":
                for k, key in _ASF.items():
                    if key in tags:
                        vals[k] = _join(str(v) for v in tags[key])
            vals["origyear"] = _read_orig(tags, kind)
        except Exception:
            pass
    cover = embedded(path)
    from_folder = False
    if not cover:
        img = folder_image(path)
        if img:
            try:
                with open(img, "rb") as fh:
                    cover, from_folder = fh.read(), True
            except OSError:
                cover = None
    return {"values": vals, "cover": cover, "cover_folder": from_folder,
            "writable": kind is not None, "cover_writable": kind in ("id3", "vorbis", "mp4")}


# ---------------------------------------------------------------- Schreiben
def _mime(data: bytes) -> str:
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


# Rahmen, die update_to_v23() loeschen wuerde, obwohl sie in der Datei standen (manche Programme schreiben
# sie auch in v2.3) -- sie bleiben erhalten. Datum, Original-Jahr und Mitwirkende werden dagegen umgewandelt.
_V24_KEEP = ("ASPI", "EQU2", "RVA2", "SEEK", "SIGN", "TDEN", "TDRL", "TDTG", "TMOO", "TPRO", "TSOA", "TSOP",
             "TSOT", "TSST")


def _save_id3(fileobj, path: str | None = None):
    """ID3 in der Version der Datei speichern. v2.3: TDRC -> TYER/TDAT, TDOR -> TORY (sonst laegen v2.4-Rahmen
    in einer v2.3-Datei, die viele Player nicht lesen)."""
    args = (path,) if path else ()
    if fileobj.tags.version[1] != 3:
        fileobj.save(*args, v2_version=4)
        return
    keep = [fr for key in _V24_KEEP for fr in fileobj.tags.getall(key)]
    fileobj.tags.update_to_v23()
    for fr in keep:
        fileobj.tags.add(fr)
    fileobj.save(*args, v2_version=3)


def _save(f):
    from mutagen.id3 import ID3
    if isinstance(f.tags, ID3):
        try:
            _save_id3(f)
            return
        except TypeError:
            pass
    f.save()


def write(path: str, values: dict[str, str], cover=KEEP):
    """Geaenderte Felder (leer = Feld loeschen) und ggf. das Cover schreiben. cover: KEEP, None (entfernen)
    oder Bilddaten (JPEG/PNG; ersetzt das Frontcover)."""
    f = _open(path)
    kind = _file_kind(f)
    if kind is None:
        raise TagError(T("Tags dieses Formats können nicht geschrieben werden"))
    if cover is not KEEP and kind == "asf":
        raise TagError(T("In WMA-Dateien kann FlacDup kein Cover schreiben"))
    if f.tags is None:
        f.add_tags()
    tags = f.tags
    for k, v in values.items():
        v = (v or "").strip() if k != "lyrics" else (v or "").strip("\n")
        if k == "origyear":
            _write_orig(tags, kind, v)
        elif kind == "id3":
            _write_id3(tags, k, v)
        elif kind == "vorbis":
            key = _VORBIS[k]
            alt = {"lyrics": "unsyncedlyrics", "comment": "description"}.get(k)
            if alt and alt in tags and key not in tags:
                key = alt
            if v:
                tags[key] = [v]
            elif key in tags:
                del tags[key]
        elif kind == "mp4":
            _write_mp4(tags, k, v)
        elif kind == "asf":
            key = _ASF[k]
            if v:
                tags[key] = [v]
            elif key in tags:
                del tags[key]
    if cover is not KEEP:
        _write_cover(f, kind, cover)
    _save(f)


def _write_id3(tags, k: str, v: str):
    from mutagen import id3
    if k == "lyrics":
        old = tags.getall("USLT")
        lang = old[0].lang if old else "XXX"
        tags.delall("USLT")
        if v:
            tags.add(id3.USLT(encoding=3, lang=lang, desc="", text=v))
        return
    if k == "comment":
        keep = [c for c in tags.getall("COMM") if c.desc]
        old = [c for c in tags.getall("COMM") if not c.desc]
        lang = old[0].lang if old else "XXX"
        tags.delall("COMM")
        for c in keep:
            tags.add(c)
        if v:
            tags.add(id3.COMM(encoding=3, lang=lang, desc="", text=[v]))
        return
    fid = _ID3[k]
    tags.delall(fid)
    if v:
        tags.add(getattr(id3, fid)(encoding=3, text=[v]))


def _num_pair(v: str) -> tuple[int, int] | None:
    a, _, b = v.partition("/")
    try:
        return int(a.strip() or 0), int(b.strip() or 0)
    except ValueError:
        raise TagError(T("„{v}“ ist keine gültige Nummer (z. B. 3 oder 3/12)", v=v)) from None


def _write_mp4(tags, k: str, v: str):
    if k in ("tracknumber", "discnumber"):
        key = "trkn" if k == "tracknumber" else "disk"
        pair = _num_pair(v) if v else None
        if pair and any(pair):
            tags[key] = [pair]
        elif key in tags:
            del tags[key]
        return
    key = _MP4[k]
    if v:
        tags[key] = [v]
    elif key in tags:
        del tags[key]


def _write_cover(f, kind: str, data: bytes | None):
    if kind == "id3":
        from mutagen.id3 import APIC
        pics = f.tags.getall("APIC")
        front = [p for p in pics if p.type == 3]
        rest = [p for p in pics if p.type != 3] if front else []
        f.tags.delall("APIC")
        for p in rest:
            f.tags.add(p)
        if data:
            f.tags.add(APIC(encoding=3, mime=_mime(data), type=3, desc="", data=data))
    elif kind == "vorbis":
        from mutagen.flac import Picture
        pic = None
        if data:
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, _mime(data), data
            _dims(pic, data)
        if hasattr(f, "clear_pictures"):                        # FLAC: eigene Bildbloecke
            pics = list(f.pictures)
            front = [p for p in pics if p.type == 3]
            rest = [p for p in pics if p.type != 3] if front else []
            f.clear_pictures()
            for p in rest:
                f.add_picture(p)
            if pic is not None:
                f.add_picture(pic)
        else:                                                   # OGG: Bild im Kommentar
            if pic is not None:
                f.tags["metadata_block_picture"] = [base64.b64encode(pic.write()).decode("ascii")]
            elif "metadata_block_picture" in f.tags:
                del f.tags["metadata_block_picture"]
    elif kind == "mp4":
        from mutagen.mp4 import MP4Cover
        if data:
            fmt = MP4Cover.FORMAT_PNG if _mime(data) == "image/png" else MP4Cover.FORMAT_JPEG
            f.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
        elif "covr" in f.tags:
            del f.tags["covr"]


def _dims(pic, data: bytes):
    try:
        from PIL import Image
        import io
        with Image.open(io.BytesIO(data)) as im:
            pic.width, pic.height = im.size
            pic.depth = 24
    except Exception:
        pass


# ---------------------------------------------------------------- Rueckgaengig
def snapshot(path: str):
    """Komplettes Tag-Objekt der Datei (inkl. Bilder) fuer restore()."""
    return _open(path)


def restore(snap, path: str):
    """Tags aus snapshot() unveraendert zurueckschreiben."""
    from mutagen.id3 import ID3
    cur = _open(path)
    if type(cur).__name__ == "FLAC" and hasattr(snap, "metadata_blocks"):
        snap.save(path)
        return
    if snap.tags is None:
        if cur.tags is not None:
            cur.delete()
        return
    if isinstance(snap.tags, ID3):
        try:
            _save_id3(snap, path)
            return
        except TypeError:
            pass
    snap.save(path)


def same_cover(a: bytes | None, b: bytes | None) -> bool:
    return (a or b"") == (b or b"")


def safe_stem(stem: str) -> bool:
    return bool(stem.strip()) and not any(c in stem for c in '\\/:*?"<>|') and stem == stem.rstrip(". ")


__all__ = ["FIELDS", "KEYS", "KEEP", "TagError", "read", "write", "snapshot", "restore", "same_cover",
           "safe_stem"]
