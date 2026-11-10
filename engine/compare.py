"""Feinvergleich eines Paares fuer die Rot/Gruen-Darstellung und das A/B-Umschalten.

Beide Dateien werden dekodiert und in Spektralbaender zerlegt. Fuer jedes Fenster
(1 s, Schritt 0,5 s) einer Datei wird die passende Stelle in der anderen gesucht --
zuerst nahe der globalen Zuordnung aus dem Abgleich (±0,5 s), bei schlechter
Uebereinstimmung weiter weg (±40 s), damit auch umgeschnittene Fassungen
(Radio-Edit, verlaengerter Mix) richtig zugeordnet werden. Ergebnis je Fenster:
Aehnlichkeit (Korrelation der Klangmuster, pegelunabhaengig) und die zugehoerige
Zeit in der anderen Datei (fuer das ms-genaue Umschalten).
"""
from __future__ import annotations

import io

import numpy as np

from .fingerprint import FPS, decode_mono, log_spectrogram

COMPARE_VERSION = 2
WIN_S = 1.0
STEP_S = 0.5
LOCAL_S = 0.5
WIDE_S = 40.0
BAND = 6                       # 6 Stufen = 2 Halbtoene je Band
# Gemessen: fremde Titel <= 0,45, dieselbe Aufnahme 0,90-0,98, andere Aufnahme desselben
# Songs (live, neu eingespielt) 0,70-0,85
GREEN = 0.85                   # ab hier "klingt gleich"
RED = 0.60                     # darunter klar verschieden (dazwischen Farbverlauf)
SILENT_DB = 55.0               # so weit unter dem lauten Teil des Stuecks gilt als Stille


def _bands(spec: np.ndarray, shift_bins: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """(Baender ohne mittlere Klangfarbe, Pegel je Rahmen in dB)."""
    if shift_bins:
        x = np.arange(spec.shape[1], dtype=np.float64)
        spec = np.stack([np.interp(x + shift_bins, x, row, left=row[0], right=row[-1]) for row in spec]) \
            .astype(np.float32)
    n = spec.shape[1] // BAND
    b = spec[:, :n * BAND].reshape(spec.shape[0], n, BAND).mean(axis=2)
    # Klangfarbe des ganzen Stuecks abziehen: verglichen wird, wie sich die Baender bewegen,
    # nicht die allgemeine Spektralneigung (die haben fast alle Aufnahmen gemeinsam)
    loud = b.max(axis=1)
    active = loud > np.percentile(loud, 95) - SILENT_DB
    return b - (b[active].mean(axis=0) if active.any() else b.mean(axis=0)), loud


def features(path: str) -> tuple[np.ndarray, float]:
    x, duration, _sr, _rep = decode_mono(path)
    return log_spectrogram(x), duration


def _windows(feat: np.ndarray, w: int) -> np.ndarray:
    """Fenster (n, w*bands), je Fenster normiert (Mittel 0, Laenge 1) nach Abzug des Pegels je Rahmen."""
    f = feat - feat.mean(axis=1, keepdims=True)
    n = f.shape[0] - w + 1
    if n <= 0:
        return np.zeros((0, w * f.shape[1]), np.float32)
    win = np.lib.stride_tricks.sliding_window_view(f, (w, f.shape[1]))[:, 0].reshape(n, -1)
    win = win - win.mean(axis=1, keepdims=True)
    norm = np.linalg.norm(win, axis=1, keepdims=True)
    return (win / np.maximum(norm, 1e-6)).astype(np.float32)


def _direction(fa: np.ndarray, fb: np.ndarray, time_scale: float, offset_s: float,
               shift_bins: float) -> tuple[np.ndarray, np.ndarray]:
    """Fuer jedes Fenster von A: (Aehnlichkeit, Zeit in B oder NaN).
    Abbildung: Zeit in B = Zeit in A * time_scale + offset_s."""
    w = int(round(WIN_S * FPS))
    step = int(round(STEP_S * FPS))
    ba, loud_a = _bands(fa)
    bb, loud_b = _bands(fb, shift_bins)   # B liegt um shift_bins hoeher: B(i + shift) entspricht A(i)
    ref = max(np.percentile(loud_a, 95), np.percentile(loud_b, 95))
    # B auf die Zeitachse von A umrechnen: B'[j] = B[j*time_scale + off]
    off_f = offset_s * FPS
    pad = int(WIDE_S * FPS) + w
    j = np.arange(-pad, ba.shape[0] + pad)
    src = np.round(j * time_scale + off_f).astype(np.int64)
    inside = (src >= 0) & (src < bb.shape[0])
    bprime = bb[np.clip(src, 0, bb.shape[0] - 1)]
    lprime = np.where(inside, loud_b[np.clip(src, 0, bb.shape[0] - 1)], -999.0)
    wa = _windows(ba, w)
    wb = _windows(bprime, w)
    valid_b = np.lib.stride_tricks.sliding_window_view(inside, w).all(axis=1)
    starts = np.arange(0, max(1, ba.shape[0] - w + 1), step)
    sim = np.full(starts.size, np.nan, np.float32)
    tmap = np.full(starts.size, np.nan, np.float64)
    local = int(round(LOCAL_S * FPS))
    wide = int(round(WIDE_S * FPS))
    for i, s in enumerate(starts):
        if s >= wa.shape[0]:
            break
        silent_a = loud_a[s:s + w].max() < ref - SILENT_DB
        best, best_l = _search(wa[s], wb, valid_b, s + pad, local)
        if (best is None or best < GREEN) and not silent_a:
            b2, l2 = _search(wa[s], wb, valid_b, s + pad, wide, stride=2)
            if b2 is not None and (best is None or b2 > best + 0.1):
                b3, l3 = _search(wa[s], wb, valid_b, s + pad + l2, 2)   # fein nachsetzen
                best, best_l = b3, l2 + l3
        if best is None:
            continue
        jb = s + pad + best_l
        silent_b = lprime[jb:jb + w].max() < ref - SILENT_DB
        if silent_a and silent_b:
            best = 1.0
        elif silent_a != silent_b:
            best = 0.0
        sim[i] = best
        centre = s + w / 2.0 + best_l
        tmap[i] = (centre * time_scale + off_f) / FPS
    return sim, tmap


def _search(vec, wb, valid_b, centre, radius, stride=1):
    lo, hi = max(0, centre - radius), min(wb.shape[0] - 1, centre + radius)
    if hi < lo:
        return None, 0
    idx = np.arange(lo, hi + 1, stride)
    idx = idx[valid_b[idx]]
    if idx.size == 0:
        return None, 0
    c = wb[idx] @ vec
    k = int(np.argmax(c))
    return float(c[k]), int(idx[k] - centre)


def compare_pair(path_a: str, path_b: str, time_scale: float, offset_s: float | None,
                 shift_bins: float) -> dict:
    """offset_s None: Versatz unbekannt (Kandidat aus dem Titelvergleich) -> schaetzen."""
    fa, dur_a = features(path_a)
    fb, dur_b = features(path_b)
    if offset_s is None:
        offset_s = estimate_offset(fa, fb)
    a_sim, a_map = _direction(fa, fb, time_scale, offset_s, shift_bins)
    b_sim, b_map = _direction(fb, fa, 1.0 / time_scale, -offset_s / time_scale, -shift_bins)

    def share(sim):  # Anteil aller Fenster, die in der anderen Datei gleich klingend vorkommen
        return float((np.nan_to_num(sim, nan=0.0) >= GREEN).mean()) if sim.size else 0.0
    both = np.concatenate([a_sim, b_sim])
    both = both[~np.isnan(both)]
    ga, gb = share(a_sim), share(b_sim)
    return {"a_sim": a_sim, "a_map": a_map, "b_sim": b_sim, "b_map": b_map,
            "dur_a": dur_a, "dur_b": dur_b, "ts": time_scale, "offset_used": offset_s,
            "a_green": ga, "b_green": gb,
            "median": float(np.median(both)) if both.size else 0.0,
            # bei einem Ausschnitt zaehlt, wie gut der kuerzere Teil in der anderen Datei steckt
            "similarity": 100.0 * max(ga, gb),
            # tatsaechliches Raster in ganzen Rahmen (0,511 s statt 0,5 s) -- sonst wandert die Zuordnung
            "step": round(STEP_S * FPS) / FPS, "win": round(WIN_S * FPS) / FPS}


def estimate_offset(fa: np.ndarray, fb: np.ndarray) -> float:
    """Grober globaler Versatz (s) ohne Vorwissen -- fuer Kandidaten aus dem Titelvergleich.
    Kreuzkorrelation der Bandverlaeufe mit 4 Rahmen je Sekunde ueber die ganze Laenge."""
    k = max(1, int(round(FPS / 4)))
    def coarse(f):
        b, _ = _bands(f)
        n = b.shape[0] // k
        c = b[:n * k].reshape(n, k, -1).mean(axis=1)
        c = c - c.mean(axis=0)
        return c / (np.linalg.norm(c, axis=1, keepdims=True) + 1e-6)
    ca, cb = coarse(fa), coarse(fb)
    if ca.shape[0] < 4 or cb.shape[0] < 4:
        return 0.0
    n = ca.shape[0] + cb.shape[0]
    size = 1 << int(np.ceil(np.log2(n)))
    acc = np.zeros(size)
    for j in range(ca.shape[1]):
        acc += np.fft.irfft(np.conj(np.fft.rfft(ca[:, j], size)) * np.fft.rfft(cb[:, j], size), size)
    lag = int(np.argmax(acc))
    if lag > size // 2:
        lag -= size
    return lag * k / FPS


def pack(res: dict) -> bytes:
    buf = io.BytesIO()
    np.savez_compressed(buf, version=COMPARE_VERSION, **{k: v for k, v in res.items()})
    return buf.getvalue()


def unpack(blob: bytes) -> dict | None:
    try:
        z = np.load(io.BytesIO(blob))
        if int(z["version"]) != COMPARE_VERSION:
            return None
        return {k: (z[k] if z[k].ndim else z[k].item()) for k in z.files if k != "version"}
    except Exception:
        return None


def map_time(res: dict, t: float, from_a: bool) -> float | None:
    """Zeit in der anderen Datei zu Zeit t (Sekunden) -- fuer das A/B-Umschalten."""
    tmap = res["a_map"] if from_a else res["b_map"]
    step, win = res["step"], res["win"]
    centres = np.arange(tmap.size) * step + win / 2.0
    ok = ~np.isnan(tmap)
    if not ok.any():
        return None
    i = int(np.argmin(np.where(ok, np.abs(centres - t), np.inf)))
    if abs(centres[i] - t) > 2.0:
        return None
    # innerhalb des Fensters mit der Laufrate weiterzaehlen
    rate = res["ts"] if from_a else 1.0 / res["ts"]
    return float(tmap[i] + (t - centres[i]) * rate)
