"""Klang-Fingerabdruck einer Datei (Phase 1).

Die Datei wird zu Mono gemischt, auf 11 025 Hz heruntergerechnet und in ein
Spektrogramm mit logarithmischer Frequenzachse (36 Stufen je Oktave) zerlegt.
Gespeichert werden nur die markanten Spektralspitzen (Zeit, Frequenz) -- daraus
werden beim Abgleich die Suchschluessel gebildet. Weil die Frequenzachse
logarithmisch ist, verschiebt eine schneller oder langsamer abgespielte Fassung
alle Spitzen nur um einen festen Betrag; der Abgleich kann sie deshalb durch
Umrechnen der Spitzen wiederfinden, ohne die Datei neu zu dekodieren.

Dazu kommt eine grobe Huellkurve fuer die Wellenform-Vorschau in der Liste.
"""
from __future__ import annotations

import zlib
from math import gcd

import numpy as np
from scipy.ndimage import maximum_filter, uniform_filter1d
from scipy.signal import resample_poly

from .decode import DecodeReport, iter_mono_blocks

FP_VERSION = 1

SR = 11025
N_FFT = 2048
HOP = 256                      # 23,2 ms je Rahmen
FPS = SR / HOP
BINS_PER_OCT = 36              # 1/3 Halbton je Stufe
F_MIN = 150.0
F_MAX = 5000.0
N_BINS = int(np.floor(BINS_PER_OCT * np.log2(F_MAX / F_MIN))) + 1   # 183
SUB = 8                        # Frequenz wird in 1/8 Stufe gespeichert

PEAK_T = 15                    # Nachbarschaft fuer lokale Maxima (Rahmen, ±)
PEAK_F = 9                     # (Stufen, ±)
PEAKS_PER_S = 14               # Zieldichte
PREVIEW_POINTS = 800

_WINDOW = np.hanning(N_FFT).astype(np.float32)
_FREQS = np.fft.rfftfreq(N_FFT, 1.0 / SR)
# Lineare Interpolation STFT-Bin -> log. Stufe
_LOG_FREQ = F_MIN * 2.0 ** (np.arange(N_BINS) / BINS_PER_OCT)
_IDX = np.searchsorted(_FREQS, _LOG_FREQ) - 1
_FRAC = ((_LOG_FREQ - _FREQS[_IDX]) / (_FREQS[_IDX + 1] - _FREQS[_IDX])).astype(np.float32)


def decode_mono(path: str) -> tuple[np.ndarray, float, int, DecodeReport]:
    """Ganze Datei als Mono mit SR Hz. Liefert (Audio, Dauer in s, Original-Abtastrate, Bericht)."""
    report = DecodeReport()
    parts: list[np.ndarray] = []
    src_sr = 0
    for block, sr in iter_mono_blocks(path, report):
        src_sr = sr
        parts.append(block)
    if not parts:
        return np.zeros(0, np.float32), 0.0, src_sr, report
    x = np.concatenate(parts)
    duration = x.shape[0] / src_sr
    if src_sr != SR:
        g = gcd(src_sr, SR)
        x = resample_poly(x, SR // g, src_sr // g).astype(np.float32)
    return x, duration, src_sr, report


def log_spectrogram(x: np.ndarray) -> np.ndarray:
    """(Rahmen, N_BINS) in dB."""
    if x.shape[0] < N_FFT:
        x = np.pad(x, (0, N_FFT - x.shape[0]))
    n = 1 + (x.shape[0] - N_FFT) // HOP
    out = np.empty((n, N_BINS), np.float32)
    step = 4096  # Rahmen je Stueck, damit der Speicher bei langen Mixen klein bleibt
    for a in range(0, n, step):
        b = min(n, a + step)
        frames = np.lib.stride_tricks.as_strided(
            x[a * HOP:], shape=(b - a, N_FFT), strides=(HOP * x.strides[0], x.strides[0]))
        mag = np.abs(np.fft.rfft(frames * _WINDOW, axis=1)).astype(np.float32)
        lo, hi = mag[:, _IDX], mag[:, _IDX + 1]
        out[a:b] = 20.0 * np.log10(lo + (hi - lo) * _FRAC + 1e-6)
    return out


def find_peaks(spec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Markante Spektralspitzen: lokales Maximum in der Nachbarschaft, nach Hervorhebung
    gegenueber dem lokalen Mittel sortiert und auf PEAKS_PER_S je Sekunde begrenzt.
    Liefert (Rahmen uint32, Frequenz in 1/SUB Stufen uint16)."""
    if spec.shape[0] < 3:
        return np.zeros(0, np.uint32), np.zeros(0, np.uint16)
    local_max = maximum_filter(spec, size=(2 * PEAK_T + 1, 2 * PEAK_F + 1), mode="nearest")
    floor = uniform_filter1d(uniform_filter1d(spec, 2 * PEAK_T + 1, axis=0, mode="nearest"),
                             2 * PEAK_F + 1, axis=1, mode="nearest")
    level = spec.max() - 70.0  # Stille und Rauschteppich ignorieren
    cand = (spec == local_max) & (spec > level)
    cand[:, 0] = cand[:, -1] = False
    t, f = np.nonzero(cand)
    if t.size == 0:
        return np.zeros(0, np.uint32), np.zeros(0, np.uint16)
    prom = spec[t, f] - floor[t, f]
    # Dichte je Sekunde begrenzen: die hervorstechendsten Spitzen jeder Sekunde behalten
    sec = (t / FPS).astype(np.int64)
    order = np.lexsort((-prom, sec))
    t, f, sec = t[order], f[order], sec[order]
    first = np.searchsorted(sec, sec, side="left")
    rank = np.arange(sec.size) - first
    keep = rank < PEAKS_PER_S
    t, f = t[keep], f[keep]
    # Frequenz parabolisch verfeinern (fuer das Umrechnen bei Tempo-/Tonhoehen-Varianten)
    a, b, c = spec[t, f - 1], spec[t, f], spec[t, f + 1]
    den = a - 2 * b + c
    delta = np.where(den < -1e-6, 0.5 * (a - c) / np.where(den < -1e-6, den, -1.0), 0.0)
    fq = np.clip(np.round((f + np.clip(delta, -0.5, 0.5)) * SUB), 0, 65535).astype(np.uint16)
    order = np.lexsort((fq, t))
    return t[order].astype(np.uint32), fq[order]


def preview(x: np.ndarray) -> np.ndarray:
    """Huellkurve (PREVIEW_POINTS Werte 0..255, Spitzenpegel je Abschnitt)."""
    if x.size == 0:
        return np.zeros(PREVIEW_POINTS, np.uint8)
    n = PREVIEW_POINTS
    edges = np.linspace(0, x.size, n + 1).astype(np.int64)
    ax = np.abs(x)
    peak = np.maximum.reduceat(ax, np.minimum(edges[:-1], x.size - 1))
    peak = np.where(edges[1:] > edges[:-1], peak, 0.0)
    return np.clip(np.round(peak / max(1e-6, float(peak.max())) * 255), 0, 255).astype(np.uint8)


def pack_peaks(t: np.ndarray, f: np.ndarray) -> bytes:
    """Zeit als Differenzen (meist klein) + Frequenz, zlib-komprimiert."""
    dt = np.diff(t, prepend=np.uint32(0)).astype(np.uint32)
    return zlib.compress(dt.tobytes() + f.astype(np.uint16).tobytes(), 6)


def unpack_peaks(blob: bytes) -> tuple[np.ndarray, np.ndarray]:
    raw = zlib.decompress(blob)
    n = len(raw) // 6
    t = np.cumsum(np.frombuffer(raw, np.uint32, n), dtype=np.uint32)
    f = np.frombuffer(raw, np.uint16, n, offset=4 * n)
    return t, f


def fingerprint_file(path: str) -> dict:
    """Wird in einem Worker-Prozess ausgefuehrt."""
    try:
        x, duration, src_sr, report = decode_mono(path)
    except Exception as exc:
        return {"path": path, "ok": False, "error": f"Datei nicht lesbar: {exc}"}
    if x.size < SR:  # unter einer Sekunde: nichts Sinnvolles zu vergleichen
        err = report.errors[0][1] if report.errors else "zu kurz oder leer"
        return {"path": path, "ok": False, "error": err, "duration": duration}
    t, f = find_peaks(log_spectrogram(x))
    return {"path": path, "ok": True, "duration": duration, "sr": src_sr,
            "n_peaks": int(t.size), "peaks": pack_peaks(t, f),
            "preview": preview(x).tobytes(),
            "decode_errors": len(report.errors)}
