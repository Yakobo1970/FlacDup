"""Suchschluessel aus Spektralspitzen und der Speicher-Index fuer den Abgleich (Phase 2).

Ein Schluessel beschreibt ein Dreieck aus einer Ankerspitze und zwei spaeteren
Spitzen: Frequenz des Ankers, Frequenzabstaende und Zeitabstaende. Ein einzelner
Treffer ist Zufall; erst viele Treffer mit demselben Zeitversatz zwischen zwei
Dateien bedeuten, dass ein Stueck Musik in beiden steckt -- auch wenn es nur ein
Ausschnitt ist.

Tempo-/Tonhoehen-Varianten: Die Spitzen einer Datei werden vor dem Bilden der
Schluessel umgerechnet (Zeit gestreckt, Frequenz verschoben), so als waere sie
schneller oder langsamer abgespielt worden.
"""
from __future__ import annotations

import numpy as np

from .fingerprint import BINS_PER_OCT, N_BINS, SUB

NEIGHBORS = 24       # so viele zeitlich folgende Spitzen werden als Partner gesichtet
FAN = 3              # Partner je Anker (daraus 3 Dreiecke)
DT_MIN, DT_MAX = 2, 63          # Rahmen
DF_MAX = 63                     # Stufen
BUCKET_CAP = 400     # Schluessel, die oefter vorkommen, sind Allerweltsklaenge und zaehlen nicht
KEY_BITS = 34        # 8 + 7 + 7 + 6 + 6


def make_hashes(t: np.ndarray, f_sub: np.ndarray, time_scale: float = 1.0,
                shift_bins: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Liefert (Schluessel uint64, Ankerzeit int32 in Rahmen).

    time_scale/shift_bins rechnen die Spitzen um: Zeit * time_scale, Frequenz + shift_bins."""
    if t.size < 3:
        return np.zeros(0, np.uint64), np.zeros(0, np.int32)
    if time_scale != 1.0:
        tt = np.round(t.astype(np.float64) * time_scale).astype(np.int64)
    else:
        tt = t.astype(np.int64)
    ff = np.round(f_sub.astype(np.float64) / SUB + shift_bins).astype(np.int64)
    ok = (ff >= 0) & (ff < N_BINS)
    tt, ff = tt[ok], ff[ok]
    order = np.lexsort((ff, tt))
    tt, ff = tt[order], ff[order]
    n = tt.size
    if n < 3:
        return np.zeros(0, np.uint64), np.zeros(0, np.int32)

    # Fuer jeden Anker die ersten FAN gueltigen Partner suchen
    partners = np.full((n, FAN), -1, np.int64)
    found = np.zeros(n, np.int64)
    idx = np.arange(n)
    for k in range(1, NEIGHBORS + 1):
        j = idx + k
        valid = j < n
        jj = np.where(valid, j, 0)
        dt = tt[jj] - tt
        df = ff[jj] - ff
        valid &= (dt >= DT_MIN) & (dt <= DT_MAX) & (np.abs(df) <= DF_MAX) & (found < FAN)
        rows = idx[valid]
        partners[rows, found[rows]] = jj[valid]
        found[rows] += 1
        if k > 4 and not np.any((found < FAN) & (idx + k < n)):
            break

    keys, anchors = [], []
    for a, b in ((0, 1), (1, 2), (0, 2)):
        sel = (partners[:, a] >= 0) & (partners[:, b] >= 0)
        i = idx[sel]
        j1, j2 = partners[sel, a], partners[sel, b]
        f1 = ff[i]
        d2 = ff[j1] - f1 + DF_MAX + 1
        d3 = ff[j2] - f1 + DF_MAX + 1
        t2 = tt[j1] - tt[i]
        t3 = tt[j2] - tt[i]
        key = ((f1.astype(np.uint64) << np.uint64(26)) | (d2.astype(np.uint64) << np.uint64(19))
               | (d3.astype(np.uint64) << np.uint64(12)) | (t2.astype(np.uint64) << np.uint64(6))
               | t3.astype(np.uint64))
        keys.append(key)
        anchors.append(tt[i].astype(np.int32))
    return np.concatenate(keys), np.concatenate(anchors)


def speed_shift(speed: float) -> tuple[float, float]:
    """Wiedergabe mit Faktor speed (>1 schneller, hoeher): Zeitfaktor und Frequenzverschiebung."""
    return 1.0 / speed, BINS_PER_OCT * np.log2(speed)


def variant_grid(enabled: bool) -> list[tuple[str, float, float]]:
    """(Bezeichnung, Zeitfaktor, Verschiebung in Stufen) fuer alle Abfrage-Durchgaenge.
    Erster Eintrag ist immer der unveraenderte Abgleich."""
    grid = [("", 1.0, 0.0)]
    if not enabled:
        return grid
    # Geschwindigkeit (Tempo + Tonhoehe gemeinsam, z. B. 33/45-Umrechnung, Pitch-Regler):
    # genau eine Stufe (1/3 Halbton, ~1,9 %) je Schritt, bis ±5 Stufen (~±10 %)
    for k in (-5, -4, -3, -2, -1, 1, 2, 3, 4, 5):
        speed = 2.0 ** (k / BINS_PER_OCT)
        ts, sh = speed_shift(speed)
        grid.append((f"Geschwindigkeit {100 * (speed - 1):+.1f} %", ts, sh))
    # Nur Tempo (Time-Stretch, Tonhoehe gleich), Schritte ~2 %
    for pct in (-8, -6, -4, -2, 2, 4, 6, 8):
        speed = 1.0 + pct / 100.0
        grid.append((f"Tempo {pct:+d} %", 1.0 / speed, 0.0))
    # Nur Tonhoehe (Pitch-Shift, Tempo gleich), ±1 und ±2 Halbtoene
    for semi in (-2, -1, 1, 2):
        grid.append((f"Tonhöhe {semi:+d} HT", 1.0, semi * BINS_PER_OCT / 12.0))
    return grid


class HashIndex:
    """Alle Schluessel aller Dateien, sortiert, im Speicher."""

    def __init__(self, keys: np.ndarray, files: np.ndarray, times: np.ndarray):
        order = np.argsort(keys)
        self.keys = keys[order]
        self.files = files[order]
        self.times = times[order]
        # Sprungtabelle ueber die oberen Bits: ein Speicherzugriff statt binaerer Suche
        self.bits = int(min(28, max(12, np.ceil(np.log2(max(2, self.keys.size))))))
        self.shift = np.uint64(KEY_BITS - self.bits)
        prefix = (self.keys >> self.shift).astype(np.int64)
        self.table = np.searchsorted(prefix, np.arange((1 << self.bits) + 1)).astype(np.int64)
        del prefix
        # Wie oft kommt der Schluessel jedes Eintrags vor (Laenge seines Laufs)?
        n = self.keys.size
        starts = np.flatnonzero(np.r_[True, self.keys[1:] != self.keys[:-1]]) if n else np.zeros(0, np.int64)
        lens = np.diff(np.r_[starts, n])
        self.run = np.repeat(np.minimum(lens, 65535).astype(np.uint16), lens)

    @classmethod
    def build(cls, parts: list[tuple[int, np.ndarray, np.ndarray]]) -> "HashIndex":
        """parts: (file_id, Schluessel, Ankerzeiten) je Datei."""
        if not parts:
            return cls(np.zeros(0, np.uint64), np.zeros(0, np.int32), np.zeros(0, np.int32))
        keys = np.concatenate([p[1] for p in parts])
        files = np.concatenate([np.full(p[1].size, p[0], np.int32) for p in parts])
        times = np.concatenate([p[2] for p in parts])
        return cls(keys, files, times)

    def __len__(self):
        return self.keys.size

    def lookup(self, qkeys: np.ndarray, qtimes: np.ndarray):
        """Alle Treffer: (Datei, Zeit in der Datei, Zeit in der Abfrage)."""
        e = np.zeros(0, np.int32)
        if qkeys.size == 0 or self.keys.size == 0:
            return e, e, e
        p = (qkeys >> self.shift).astype(np.int64)
        lo, hi = self.table[p], self.table[p + 1]
        cnt = hi - lo
        use = (cnt > 0) & (cnt <= BUCKET_CAP * 4)
        lo, cnt, qk, qt = lo[use], cnt[use], qkeys[use], qtimes[use]
        total = int(cnt.sum())
        if total == 0:
            return e, e, e
        # alle Eintraege der Spruengtabellen-Faecher aufzaehlen, dann exakt vergleichen
        pos = np.repeat(lo - np.cumsum(cnt) + cnt, cnt) + np.arange(total)
        qk = np.repeat(qk, cnt)
        qt = np.repeat(qt, cnt)
        hit = self.keys[pos] == qk
        pos, qk, qt = pos[hit], qk[hit], qt[hit]
        # Allerweltsschluessel (zu viele Eintraege) verwerfen
        keep = self.run[pos] <= BUCKET_CAP
        pos, qt = pos[keep], qt[keep]
        return self.files[pos], self.times[pos], qt
