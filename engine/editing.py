"""Einfache Audiobearbeitung: Tracks teilen und Bereiche herausschneiden, ohne Qualitaetsverlust.

* MP3: Die Original-Frames werden nur umkopiert (keine Neukodierung). Schnitte rasten dadurch
  auf MP3-Frames ein (1152 Samples, bei 44,1 kHz ca. 26 ms).
* FLAC, WAV, AIFF: sample-genau und verlustfrei (gleiche Bittiefe wie das Original).
* Tags: alle Tags inkl. Cover werden vom Original uebernommen, die einfachen Felder (Titel,
  Interpret, ...) lassen sich je Teilstueck ueberschreiben.
"""
from __future__ import annotations

import os
from pathlib import Path

import soundfile as sf

LOSSLESS_EXT = {".flac", ".wav", ".aif", ".aiff"}
COPY_EXT = {".mp3"}
EDITABLE_EXT = LOSSLESS_EXT | COPY_EXT

# Einfache Tag-Felder (mutagen-"easy"-Schluessel) mit Anzeigename
TAG_FIELDS = [("title", "Titel"), ("artist", "Interpret"), ("album", "Album"),
              ("albumartist", "Album-Interpret"), ("tracknumber", "Track"), ("date", "Jahr"),
              ("genre", "Genre")]

MIN_PART_S = 0.05


class EditError(Exception):
    pass


def can_edit(path: str) -> bool:
    return Path(path).suffix.lower() in EDITABLE_EXT


# ---------------------------------------------------------------- Schnittplan
def plan_parts(duration: float, splits: list[float], removed: list[tuple[float, float]]
               ) -> list[list[tuple[float, float]]]:
    """Aus Schnittpunkten und entfernten Bereichen die Teilstuecke berechnen.
    Ergebnis: je Teilstueck die Liste der behaltenen Bereiche (werden aneinandergehaengt).
    Ein Teilstueck, das komplett entfernt wurde, ist eine leere Liste."""
    bounds = [0.0] + sorted(s for s in splits if 0.0 < s < duration) + [duration]
    parts = []
    for a, b in zip(bounds[:-1], bounds[1:]):
        keep = [(a, b)]
        for ra, rb in sorted(removed):
            nxt = []
            for x, y in keep:
                if rb <= x or ra >= y:
                    nxt.append((x, y))
                    continue
                if ra > x:
                    nxt.append((x, ra))
                if rb < y:
                    nxt.append((rb, y))
            keep = nxt
        parts.append([(x, y) for x, y in keep if y - x >= MIN_PART_S])
    return parts


# ---------------------------------------------------------------- Tags
def read_tags(path: str) -> dict[str, str]:
    """Einfache Tags des Originals (erster Wert je Feld)."""
    try:
        from mutagen import File
        f = File(path, easy=True)
    except Exception:
        return {}
    if f is None or f.tags is None:
        return {}
    out = {}
    for key, _ in TAG_FIELDS:
        try:
            vals = f.tags.get(key)
        except Exception:
            vals = None
        if vals:
            out[key] = str(vals[0])
    return out


def copy_tags(src: str, dst: str, overrides: dict[str, str]):
    """Alle Tags (inkl. Cover) von src nach dst kopieren, dann die einfachen Felder setzen."""
    ext = Path(dst).suffix.lower()
    if ext == ".mp3":
        from mutagen.easyid3 import EasyID3
        from mutagen.id3 import ID3, ID3NoHeaderError
        try:
            tags = ID3(src)
            version = tags.version[1] if tags.version[1] in (3, 4) else 4
            tags.save(dst, v2_version=version)
        except ID3NoHeaderError:
            ID3().save(dst)
        e = EasyID3(dst)
        _apply(e, overrides)
        e.save()
    elif ext == ".flac":
        from mutagen.flac import FLAC
        s, d = FLAC(src), FLAC(dst)
        d.delete()
        d.clear_pictures()
        if s.tags:
            for key, vals in s.tags.as_dict().items():
                d[key] = vals
        for pic in s.pictures:
            d.add_picture(pic)
        _apply(d, overrides)
        d.save()
    else:  # WAV/AIFF: Tags, soweit mutagen sie dort kann
        try:
            from mutagen import File
            f = File(dst, easy=True)
            if f is not None:
                if f.tags is None:
                    f.add_tags()
                _apply(f, overrides)
                f.save()
        except Exception:
            pass


def write_tags(path: str, overrides: dict[str, str]):
    """Einfache Felder direkt in der Datei aendern (alle anderen Tags und das Cover bleiben)."""
    from mutagen import File
    f = File(path, easy=True)
    if f is None:
        raise EditError("Tags dieses Formats können nicht geschrieben werden")
    if f.tags is None:
        f.add_tags()
    _apply(f, overrides)
    f.save()


def _apply(tagobj, overrides: dict[str, str]):
    for key, val in overrides.items():
        try:
            if val is None or str(val).strip() == "":
                if key in tagobj:
                    del tagobj[key]
            else:
                tagobj[key] = [str(val).strip()]
        except Exception:
            pass


# ---------------------------------------------------------------- Schreiben
def write_part(src: str, dst: str, ranges: list[tuple[float, float]], tags: dict[str, str]):
    """Ein Teilstueck (Bereiche aneinandergehaengt) verlustfrei nach dst schreiben."""
    if not ranges:
        raise EditError("Teilstück ist leer")
    ext = Path(src).suffix.lower()
    if Path(dst).suffix.lower() != ext:
        raise EditError("Die Dateiendung muss der des Originals entsprechen")
    tmp = dst + ".flacdup.tmp" + ext
    try:
        if ext in LOSSLESS_EXT:
            _write_lossless(src, tmp, ranges)
        elif ext in COPY_EXT:
            _write_mp3_copy(src, tmp, ranges)
        else:
            raise EditError(f"Format {ext} wird zum Bearbeiten nicht unterstützt")
        copy_tags(src, tmp, tags)
        os.replace(tmp, dst)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _write_lossless(src: str, dst: str, ranges):
    info = sf.info(src)
    sr = info.samplerate
    block = 1 << 18
    with sf.SoundFile(src) as f, sf.SoundFile(dst, "w", samplerate=sr, channels=info.channels,
                                              subtype=info.subtype, format=info.format) as out:
        for a, b in ranges:
            start, stop = int(round(a * sr)), min(int(round(b * sr)), f.frames)
            f.seek(start)
            remaining = stop - start
            while remaining > 0:
                data = f.read(min(block, remaining), dtype="int32", always_2d=True)
                if data.shape[0] == 0:
                    break
                out.write(data)
                remaining -= data.shape[0]


def _write_mp3_copy(src: str, dst: str, ranges):
    """MP3-Frames umkopieren: ein Frame wird uebernommen, wenn seine Mitte in einem Bereich liegt."""
    import av
    with av.open(src, metadata_errors="replace") as inp:
        ist = inp.streams.audio[0]
        tb = ist.time_base
        sr = ist.rate or 44100
        frame_ticks = int(round(1152 / sr / float(tb))) or 1
        out = av.open(dst, "w", format="mp3")
        try:
            ost = out.add_stream_from_template(ist)
            next_pts = 0
            for pkt in inp.demux(ist):
                if pkt.size == 0 or pkt.pts is None:
                    continue
                dur = pkt.duration or frame_ticks
                mid = float((pkt.pts + dur / 2) * tb)
                if not any(a <= mid < b for a, b in ranges):
                    continue
                pkt.stream = ost
                pkt.pts = pkt.dts = next_pts
                next_pts += dur
                out.mux(pkt)
        finally:
            out.close()


def part_duration(ranges) -> float:
    return sum(b - a for a, b in ranges)
