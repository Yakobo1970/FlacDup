"""Auswertung der Index-Treffer einer Abfragedatei: Welche anderen Dateien teilen
mit ihr ein Stueck Musik, mit welchem Zeitversatz, ueber welchen Bereich, und wie viel
davon stimmt ueberein?"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .fingerprint import FPS
from .hashing import HashIndex

MIN_VOTES = 20           # Mindestzahl uebereinstimmender Schluessel mit gleichem Versatz
MIN_SPAN_S = 8.0         # gemeinsamer Bereich mindestens so lang
COVER_BIN_S = 2.0        # Raster fuer die Abdeckung entlang der Zeit
_OFF = 1 << 21


@dataclass
class Match:
    other: int            # file_id der anderen Datei
    votes: int
    offset_s: float       # Zeit in other = Zeit in Abfrage (umgerechnet) + offset
    q_start: float        # gemeinsamer Bereich in der Abfrage (s, Originalzeit)
    q_end: float
    o_start: float        # gemeinsamer Bereich in der anderen Datei
    o_end: float
    density: float        # Anteil der Abfrage-Schluessel im Bereich, die getroffen haben
    variant: str
    time_scale: float     # Zeit in other = Zeit in Abfrage * time_scale + offset
    shift_bins: float


def evaluate(index: HashIndex, query_id: int, qkeys: np.ndarray, qtimes: np.ndarray,
             time_scale: float = 1.0, shift_bins: float = 0.0, variant: str = "",
             skip: np.ndarray | None = None) -> list[Match]:
    """skip: bool-Feld nach file_id -- diese Dateien nicht melden (schon abgeglichen)."""
    files, tf, tq = index.lookup(qkeys, qtimes)
    keep = files != query_id
    if skip is not None:
        keep &= ~skip[files]
    files, tf, tq = files[keep], tf[keep], tq[keep]
    if files.size < MIN_VOTES:
        return []
    d = tf.astype(np.int64) - tq.astype(np.int64)
    vk = files.astype(np.int64) * (2 * _OFF) + (d + _OFF)
    uk, cnt = np.unique(vk, return_counts=True)
    # Nachbar-Versaetze (±1 Rahmen) mitzaehlen: Spitzen wackeln um einen Rahmen
    last = uk.size - 1
    left = np.minimum(np.searchsorted(uk, uk - 1), last)
    right = np.minimum(np.searchsorted(uk, uk + 1), last)
    smooth = (cnt + np.where(uk[left] == uk - 1, cnt[left], 0)
              + np.where(uk[right] == uk + 1, cnt[right], 0))
    good = smooth >= MIN_VOTES
    if not np.any(good):
        return []
    uk, smooth = uk[good], smooth[good]
    ufile = uk // (2 * _OFF)
    # je Datei der staerkste Versatz
    order = np.lexsort((-smooth, ufile))
    ufile, uk, smooth = ufile[order], uk[order], smooth[order]
    first = np.r_[True, ufile[1:] != ufile[:-1]]
    out = []
    qset_times = np.sort(qtimes)
    for fid, key, votes in zip(ufile[first], uk[first], smooth[first]):
        off = int(key - fid * (2 * _OFF) - _OFF)
        sel = (files == fid) & (np.abs(d - off) <= 1)
        mq = tq[sel]
        lo, hi = _span(mq)
        if (hi - lo) / FPS < MIN_SPAN_S:
            continue
        total = np.searchsorted(qset_times, hi, "right") - np.searchsorted(qset_times, lo, "left")
        density = float(np.unique(mq).size) / max(1, total)
        # Zeiten zurueck auf die Originalachse der Abfrage
        q0, q1 = lo / time_scale, hi / time_scale
        out.append(Match(int(fid), int(votes), off / FPS,
                         q0 / FPS, q1 / FPS, (lo + off) / FPS, (hi + off) / FPS,
                         density, variant, time_scale, shift_bins))
    return out


def _span(times: np.ndarray) -> tuple[int, int]:
    """Zusammenhaengender Bereich der Treffer: Ausreisser an den Raendern abschneiden,
    indem nur Abschnitte mit mehreren Treffern je Raster zaehlen."""
    t = np.sort(times)
    if t.size < 4:
        return int(t[0]), int(t[-1])
    b = (t / (COVER_BIN_S * FPS)).astype(np.int64)
    ub, c = np.unique(b, return_counts=True)
    dense = ub[c >= 2]
    if dense.size == 0:
        return int(t[0]), int(t[-1])
    lo_b, hi_b = dense.min(), dense.max()
    inside = t[(b >= lo_b) & (b <= hi_b)]
    return int(inside[0]), int(inside[-1])
