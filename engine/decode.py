"""Einheitliches Dekodieren aller unterstuetzten Audioformate.

Verlustfreie/einfache Formate (FLAC, WAV, AIFF, OGG-Vorbis) laufen ueber
libsndfile (soundfile), alles andere (MP3, M4A/AAC/ALAC, OPUS, WMA, APE, WV ...)
ueber PyAV (gebuendeltes FFmpeg). Faellt soundfile beim Oeffnen aus, wird PyAV
als Rueckfallebene genutzt.

Dekodierfehler mitten in der Datei werden nicht als Ausnahme durchgereicht,
sondern als Zeitpunkte gesammelt -- sie sind selbst ein Befund.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".wav", ".aif", ".aiff", ".ogg", ".oga", ".opus",
    ".m4a", ".aac", ".alac", ".mp4", ".wma", ".ape", ".wv", ".mpc", ".dsf",
}
_SNDFILE_EXTENSIONS = {".flac", ".wav", ".aif", ".aiff", ".ogg", ".oga"}

BLOCK_FRAMES = 1 << 18  # ~6 s bei 44,1 kHz


@dataclass
class AudioInfo:
    sr: int
    channels: int
    frames: int  # erwartete Laenge laut Header (0 = unbekannt)
    codec: str


@dataclass
class DecodeReport:
    """Wird waehrend des Streamens befuellt."""
    frames_read: int = 0
    errors: list[tuple[float, str]] = field(default_factory=list)  # (Sekunde, Text)


def _use_sndfile(path: Path) -> bool:
    return path.suffix.lower() in _SNDFILE_EXTENSIONS


# ---------------------------------------------------------------- Info

def probe(path: str | Path) -> AudioInfo:
    path = Path(path)
    if _use_sndfile(path):
        try:
            info = sf.info(str(path))
            return AudioInfo(info.samplerate, info.channels, info.frames,
                             f"{info.format} {info.subtype}".strip())
        except Exception:
            pass
    import av
    with av.open(str(path), metadata_errors="replace") as container:
        stream = container.streams.audio[0]
        frames = 0
        if stream.duration is not None and stream.time_base is not None:
            frames = int(round(float(stream.duration * stream.time_base) * stream.rate))
        elif container.duration is not None:
            frames = int(round(container.duration / 1_000_000 * stream.rate))
        name = stream.codec_context.name
        for internal, nice in (("mp3float", "mp3"), ("aac_fixed", "aac"), ("wmav2", "wma"),
                               ("wmapro", "wma")):
            if name == internal:
                name = nice
        return AudioInfo(stream.rate, stream.channels, frames, name)


# ---------------------------------------------------------------- Streaming (mono)

def iter_mono_blocks(path: str | Path, report: DecodeReport) -> Iterator[tuple[np.ndarray, int]]:
    """Wie iter_blocks, aber zu Mono gemischt."""
    for block, sr in iter_blocks(path, report):
        yield (block.mean(axis=1, dtype=np.float32) if block.shape[1] > 1 else block[:, 0]), sr


def iter_blocks(path: str | Path, report: DecodeReport) -> Iterator[tuple[np.ndarray, int]]:
    """Liefert (float32-Block (frames, kanaele<=2), samplerate). Mehr als zwei Kanaele
    werden auf die ersten beiden reduziert. Fehler landen in `report`."""
    path = Path(path)
    if _use_sndfile(path):
        try:
            f = sf.SoundFile(str(path))
        except Exception:
            f = None
        if f is not None:
            yield from _iter_sndfile(f, report)
            return
    yield from _iter_av(path, report)


def _iter_sndfile(f: sf.SoundFile, report: DecodeReport):
    sr = f.samplerate
    with f:
        while True:
            try:
                block = f.read(BLOCK_FRAMES, dtype="float32", always_2d=True)
            except Exception as exc:  # libsndfile/libFLAC meldet kaputten Frame
                report.errors.append((report.frames_read / sr, f"Dekodierfehler: {exc}"))
                return
            if block.shape[0] == 0:
                return
            block = block[:, :2]
            report.frames_read += block.shape[0]
            yield np.ascontiguousarray(block), sr


def _iter_av(path: Path, report: DecodeReport):
    import av
    try:
        container = av.open(str(path), metadata_errors="replace")
    except Exception as exc:
        report.errors.append((0.0, f"Datei nicht lesbar: {exc}"))
        return
    with container:
        stream = container.streams.audio[0]
        sr = stream.rate
        nch = 2 if (stream.channels or 1) >= 2 else 1
        resampler = av.AudioResampler(format="flt", layout="stereo" if nch == 2 else "mono", rate=sr)
        pending: list[np.ndarray] = []
        pending_len = 0
        last_err_time = -1.0

        def flush():
            nonlocal pending, pending_len
            out = np.concatenate(pending) if len(pending) > 1 else pending[0]
            pending, pending_len = [], 0
            report.frames_read += out.shape[0]
            return out

        packets = container.demux(stream)
        while True:
            try:
                packet = next(packets)
            except StopIteration:
                break
            except Exception as exc:  # Demuxer-Fehler: Datei ab hier unlesbar
                report.errors.append((report.frames_read / sr, f"Container-Fehler: {exc}"))
                break
            try:
                frames = packet.decode()
            except Exception as exc:
                t = (report.frames_read + pending_len) / sr
                if t - last_err_time > 0.5:  # Fehlerserien zusammenfassen
                    report.errors.append((t, f"Dekodierfehler: {exc}"))
                    last_err_time = t
                continue
            for frame in frames:
                for rf in resampler.resample(frame):
                    arr = rf.to_ndarray().reshape(-1, nch).astype(np.float32, copy=False)
                    pending.append(arr)
                    pending_len += arr.shape[0]
            if pending_len >= BLOCK_FRAMES:
                yield flush(), sr
        for rf in resampler.resample(None):
            arr = rf.to_ndarray().reshape(-1, nch).astype(np.float32, copy=False)
            pending.append(arr)
            pending_len += arr.shape[0]
        if pending_len:
            yield flush(), sr


# ---------------------------------------------------------------- Ausschnitt (Wiedergabe)

def read_segment(path: str | Path, t0: float, t1: float) -> tuple[np.ndarray, int]:
    """Liest [t0, t1) Sekunden als float32 (frames, channels) -- fuer die Wiedergabe."""
    path = Path(path)
    t0 = max(0.0, t0)
    if _use_sndfile(path):
        try:
            with sf.SoundFile(str(path)) as f:
                sr = f.samplerate
                start = int(t0 * sr)
                f.seek(min(start, f.frames))
                data = f.read(max(0, int((t1 - t0) * sr)), dtype="float32", always_2d=True)
                return data, sr
        except Exception:
            pass
    import av
    with av.open(str(path), metadata_errors="replace") as container:
        stream = container.streams.audio[0]
        sr = stream.rate
        channels = min(stream.channels or 2, 2)
        layout = "stereo" if channels == 2 else "mono"
        resampler = av.AudioResampler(format="flt", layout=layout, rate=sr)
        try:
            container.seek(int(t0 / float(stream.time_base)), stream=stream, backward=True)
        except Exception:
            container.seek(0)
        chunks: list[np.ndarray] = []
        pos_known: float | None = None
        collected = 0
        need = int((t1 - t0) * sr)
        for packet in container.demux(stream):
            try:
                frames = packet.decode()
            except Exception:
                continue
            for frame in frames:
                if pos_known is None:
                    pos_known = float(frame.pts * stream.time_base) if frame.pts is not None else t0
                for rf in resampler.resample(frame):
                    arr = rf.to_ndarray().reshape(-1, channels).astype(np.float32, copy=False)
                    chunks.append(arr)
                    collected += arr.shape[0]
            if pos_known is not None and collected - int(max(0.0, t0 - pos_known) * sr) >= need:
                break
        if not chunks:
            return np.zeros((0, channels), np.float32), sr
        data = np.concatenate(chunks)
        skip = int(max(0.0, t0 - (pos_known or t0)) * sr)
        return data[skip: skip + need], sr
