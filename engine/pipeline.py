"""Die beiden Arbeitsphasen, jeweils fortsetzbar.

Phase 1 (Index): Dateien sammeln, neue/veraenderte als 'pending' eintragen, dann in
Worker-Prozessen fingerprinten. Jede fertige Datei wird sofort gespeichert.

Phase 2 (Abgleich): Alle Fingerabdruecke in einen Speicher-Index laden und jede noch
nicht abgeglichene Datei dagegen abfragen. Die Paare einer Datei und ihr Haken
'abgeglichen' werden in einer Transaktion geschrieben.
"""
from __future__ import annotations

import os
import re
import threading
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from typing import Callable

import numpy as np

from . import compare as compare_mod
from . import matcher
from .db import Database
from .decode import AUDIO_EXTENSIONS, probe
from .fingerprint import FP_VERSION, fingerprint_file, unpack_peaks
from .hashing import HashIndex, make_hashes, variant_grid
from .tags import read_tags

MATCH_VERSION = 1
MOVE_DURATION_TOL = 0.5
IDENTICAL_COVER = 0.9     # gemeinsamer Bereich deckt beide Dateien zu mind. 90 % ab
CONTAINED_COVER = 0.8     # ... oder eine davon zu mind. 80 %


def collect_files(root: str, stop: threading.Event | None = None) -> list[tuple[str, int, float]]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        if stop is not None and stop.is_set():
            break
        dirnames.sort(key=str.lower)
        for name in sorted(filenames, key=str.lower):
            if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                p = os.path.normpath(os.path.join(dirpath, name))
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out.append((p, st.st_size, st.st_mtime))
    return out


def default_workers() -> int:
    return max(1, (os.cpu_count() or 4) - 2)


def index_worker(path: str) -> tuple[dict, dict]:
    res = fingerprint_file(path)
    return res, read_tags(path)


class Indexer:
    """Phase 1."""

    def __init__(self, db: Database, targets: list[str], workers: int,
                 on_status: Callable[[str], None], on_progress: Callable[[int, int], None]):
        self.db = db
        self.targets = [os.path.normpath(p) for p in targets]
        self.workers = max(1, workers)
        self.on_status = on_status
        self.on_progress = on_progress
        self.stop = threading.Event()

    def _gather(self) -> list[tuple[str, int, float]]:
        files, seen = [], set()
        for target in self.targets:
            if os.path.isdir(target):
                self.db.add_root(target)
                found = collect_files(target, self.stop)
            elif os.path.isfile(target) and os.path.splitext(target)[1].lower() in AUDIO_EXTENSIONS:
                self.db.add_root(os.path.dirname(target))
                st = os.stat(target)
                found = [(target, st.st_size, st.st_mtime)]
            else:
                found = []
            for item in found:
                if item[0] not in seen:
                    seen.add(item[0])
                    files.append(item)
        return files

    def _handle_moves(self, files, states) -> int:
        """Eintraege, deren Datei fehlt, ziehen zu einer neu gefundenen Datei mit gleichem Namen
        und gleicher Dauer um (Fingerabdruck und Pruefstand bleiben)."""
        under = tuple(os.path.join(t, "") for t in self.targets if os.path.isdir(t))
        gone = {}
        for path, (fid, _s, _m, status, _v) in states.items():
            if status != "missing" and under and not path.startswith(under):
                continue
            if not os.path.exists(path):
                gone.setdefault(os.path.basename(path).lower(), []).append((fid, path))
        if not gone:
            return 0
        known = self.db.load_files([fid for lst in gone.values() for fid, _ in lst])
        moved = 0
        for path, size, mtime in files:
            if path in states:
                continue
            cands = gone.get(os.path.basename(path).lower())
            if not cands:
                continue
            try:
                info = probe(path)
                dur = info.frames / info.sr if info.sr and info.frames else 0.0
            except Exception:
                continue
            hits = [c for c in cands if abs((known[c[0]]["duration"] or -99) - dur) <= MOVE_DURATION_TOL]
            if len(hits) == 1:
                self.db.relocate(hits[0][0], path, size, mtime)
                states[path] = states.pop(hits[0][1])
                cands.remove(hits[0])
                moved += 1
        self.db.mark_missing([fid for lst in gone.values() for fid, _ in lst])
        return moved

    def run(self):
        self.on_status("Dateien werden gesammelt …")
        files = self._gather()
        if self.stop.is_set():
            return
        states = self.db.file_states()
        moved = self._handle_moves(files, states)
        new = [f for f in files
               if f[0] not in states or states[f[0]][1] != f[1] or abs((states[f[0]][2] or 0) - f[2]) > 1]
        self.db.register(new)
        states = self.db.file_states()
        wanted = {f[0] for f in files}
        todo = [p for p in wanted
                if states[p][3] == "pending" or states[p][4] != FP_VERSION]
        todo.sort(key=str.lower)
        total, done = len(files), len(files) - len(todo)
        extra = f" · {moved} verschobene Dateien wiedererkannt" if moved else ""
        self.on_status(f"Index: {total} Audiodateien, davon {len(todo)} zu analysieren{extra}")
        self.on_progress(done, total)
        if not todo:
            return
        t0, n0 = time.time(), done
        with ProcessPoolExecutor(max_workers=self.workers) as ex:
            pending = {}
            it = iter(todo)

            def submit_next():
                try:
                    p = next(it)
                except StopIteration:
                    return False
                pending[ex.submit(index_worker, p)] = p
                return True

            for _ in range(self.workers * 2):
                if not submit_next():
                    break
            while pending:
                finished, _ = wait(list(pending), timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in finished:
                    p = pending.pop(fut)
                    try:
                        res, tags = fut.result()
                    except Exception as exc:  # Worker abgestuerzt (z. B. Speicher)
                        res, tags = {"path": p, "ok": False, "error": f"Analyse abgebrochen: {exc}"}, {}
                    self.db.store_fingerprint(res, FP_VERSION, tags)
                    done += 1
                    self.on_progress(done, total)
                    if not self.stop.is_set():
                        submit_next()
                if finished:
                    rate = (done - n0) / max(1e-6, time.time() - t0)
                    left = (total - done) / rate if rate > 0 else 0
                    self.on_status(f"Index: {done} von {total} Dateien · {rate:.1f}/s · "
                                   f"noch ca. {_fmt_dur(left)}")
                if self.stop.is_set():
                    for fut in pending:
                        fut.cancel()
                    ex.shutdown(wait=False, cancel_futures=True)
                    self.on_status("Index angehalten – beim nächsten Start geht es hier weiter")
                    return
        self.on_status(f"Index fertig: {total} Dateien")


def _fmt_dur(s: float) -> str:
    s = int(s)
    if s >= 3600:
        return f"{s // 3600} h {s % 3600 // 60:02d} min"
    if s >= 60:
        return f"{s // 60} min"
    return f"{s} s"


def classify(m: matcher.Match, q_dur: float, o_dur: float) -> tuple[str, float, float]:
    """Art des Fundes und Abdeckung beider Dateien durch den gemeinsamen Bereich."""
    cq = (m.q_end - m.q_start) / max(1e-6, q_dur)
    co = (m.o_end - m.o_start) / max(1e-6, o_dur)
    if m.variant:
        return "variante", cq, co
    if cq >= IDENTICAL_COVER and co >= IDENTICAL_COVER:
        return "identisch", cq, co
    if cq >= CONTAINED_COVER or co >= CONTAINED_COVER:
        return "ausschnitt", cq, co
    return "teilweise", cq, co


def _row(q: int, m: matcher.Match, durs: dict) -> dict:
    kind, _cq, _co = classify(m, durs.get(q) or 0, durs.get(m.other) or 0)
    r = {"votes": m.votes, "density": m.density, "similarity": None, "kind": kind, "variant": m.variant}
    if q < m.other:
        r.update(a_id=q, b_id=m.other, offset_s=m.offset_s, time_scale=m.time_scale,
                 shift_bins=m.shift_bins, a_start=m.q_start, a_end=m.q_end,
                 b_start=m.o_start, b_end=m.o_end)
    else:  # kanonisch a < b: Abbildung umkehren
        r.update(a_id=m.other, b_id=q, offset_s=-m.offset_s / m.time_scale, time_scale=1.0 / m.time_scale,
                 shift_bins=-m.shift_bins, a_start=m.o_start, a_end=m.o_end,
                 b_start=m.q_start, b_end=m.q_end)
    return r


class Matcher:
    """Phase 2."""

    def __init__(self, db: Database, variants: bool,
                 on_status: Callable[[str], None], on_progress: Callable[[int, int], None]):
        self.db = db
        self.variants = variants
        self.on_status = on_status
        self.on_progress = on_progress
        self.stop = threading.Event()

    def run(self):
        self.on_status("Abgleich: Fingerabdrücke werden geladen …")
        peaks: dict[int, tuple[np.ndarray, np.ndarray]] = {}
        parts = []
        todo = []
        version = MATCH_VERSION * 10 + (1 if self.variants else 0)
        for n, (fid, blob, mv) in enumerate(self.db.iter_fingerprints()):
            if self.stop.is_set():
                return
            t, f = unpack_peaks(blob)
            k, a = make_hashes(t, f)
            parts.append((fid, k, a))
            if mv != version:
                todo.append(fid)
                peaks[fid] = (t, f)
            if n % 2000 == 0:
                self.on_status(f"Abgleich: {n} Fingerabdrücke geladen …")
        if not todo:
            self.on_status("Abgleich: nichts zu tun – alle Dateien sind abgeglichen")
            return
        self.on_status(f"Abgleich: Index aus {len(parts)} Dateien wird aufgebaut …")
        index = HashIndex.build(parts)
        durs = {r[0]: r[1] for r in self.db.conn.execute("SELECT id, duration FROM files WHERE status='done'")}
        del parts
        grid = variant_grid(self.variants)
        # Wer in diesem Lauf schon abgefragt wurde, hat jede noch folgende Datei bereits gesehen
        queried = np.zeros(int(max(durs)) + 1 if durs else 1, bool)
        # Fortschritt ueber alle Dateien: bereits abgeglichene zaehlen mit (wie beim Index)
        total = max(len(durs), len(todo))
        base = total - len(todo)
        self.on_progress(base, total)
        t0 = time.time()
        for n, q in enumerate(todo):
            if self.stop.is_set():
                self.on_status("Abgleich angehalten – beim nächsten Start geht es hier weiter")
                return
            t, f = peaks.pop(q)
            best: dict[int, matcher.Match] = {}
            for name, ts, sh in grid:
                k, a = make_hashes(t, f, ts, sh)
                for m in matcher.evaluate(index, q, k, a, ts, sh, name, skip=queried):
                    old = best.get(m.other)
                    # die unveraenderte Fassung gewinnt, solange eine Variante nicht deutlich staerker ist
                    need = 1.5 if (m.variant and old is not None and not old.variant) else 1.0
                    if old is None or m.votes > old.votes * need:
                        best[m.other] = m
            self.db.store_matches(q, [_row(q, m, durs) for m in best.values()], version)
            queried[q] = True
            if n % 20 == 0 or n == len(todo) - 1:
                self.on_progress(base + n + 1, total)
                rate = (n + 1) / max(1e-6, time.time() - t0)
                self.on_status(f"Abgleich: {base + n + 1} von {total} Dateien · "
                               f"noch ca. {_fmt_dur((len(todo) - n - 1) / rate)}")
        self.on_status(f"Abgleich fertig: {total} Dateien abgeglichen")


# ---------------------------------------------------------------- Phase 3: Feinvergleich

TITLE_GROUP_MAX = 15        # groessere Gruppen gleichen Titels ("Intro") werden nicht paarweise verglichen
TITLE_DUR_RATIO = 3.0       # Laengenverhaeltnis, ab dem es keine Fassung desselben Titels mehr ist
VERSION_MEDIAN = 0.60       # Median-Aehnlichkeit fuer "andere Aufnahme" (fremde Titel ~0,35)
_BRACKETS = re.compile(r"[\(\[\{].*?[\)\]\}]")
_FEAT = re.compile(r"\b(feat\.?|featuring|ft\.)\s.*$")
_NONWORD = re.compile(r"[\W_]+", re.UNICODE)


def norm_name(s: str | None, title: bool = False) -> str:
    """Fuer den Titelvergleich: Klammerzusaetze (Remix, Live, Edit ...), feat., Satzzeichen und
    Zaehlsuffixe von Kopien ("Titel1") entfernen."""
    if not s:
        return ""
    s = _FEAT.sub("", _BRACKETS.sub(" ", s.lower()))
    s = _NONWORD.sub(" ", s).strip()
    if title:
        s = re.sub(r"(?<=[a-z])\d{1,2}$", "", s)
    else:
        s = re.sub(r"^the\s+", "", s)
    return s.replace(" ", "")


def title_candidates(db: Database) -> list[tuple[int, int]]:
    groups: dict[tuple[str, str], list[tuple[int, float]]] = {}
    for fid, artist, title, dur in db.title_groups():
        key = (norm_name(artist), norm_name(title, True))
        if key[0] and key[1]:
            groups.setdefault(key, []).append((fid, dur or 0.0))
    have = db.existing_pairs()
    out = []
    for members in groups.values():
        if not 2 <= len(members) <= TITLE_GROUP_MAX:
            continue
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                (a, da), (b, dbb) = sorted((members[i], members[j]))
                if min(da, dbb) <= 0 or max(da, dbb) / min(da, dbb) > TITLE_DUR_RATIO:
                    continue
                if (a, b) not in have:
                    out.append((a, b))
    return out


def classify_compare(res: dict, variant: str) -> str:
    ga, gb = res["a_green"], res["b_green"]
    hi = max(ga, gb)
    if variant and hi >= 0.5:
        return "variante"
    if ga >= 0.9 and gb >= 0.9:
        return "identisch"
    if hi >= 0.9:
        return "ausschnitt"
    if hi >= 0.25:
        return "teilweise"
    if res["median"] >= VERSION_MEDIAN:
        return "version"
    return "schwach"


def compare_worker(job: tuple) -> tuple[int, str, dict | None, bytes | None, str | None]:
    pid, pa, pb, ts, off, sh, variant, source = job
    try:
        res = compare_mod.compare_pair(pa, pb, ts or 1.0, None if source == "title" else (off or 0.0),
                                       sh or 0.0)
    except Exception as exc:
        return pid, "", None, None, str(exc)
    return pid, classify_compare(res, variant or ""), res, compare_mod.pack(res), None


class Comparer:
    """Phase 3: Feinvergleich aller Paare in Worker-Prozessen, jedes Ergebnis sofort gespeichert."""

    def __init__(self, db: Database, workers: int, titles: bool,
                 on_status: Callable[[str], None], on_progress: Callable[[int, int], None]):
        self.db = db
        self.workers = max(1, workers)
        self.titles = titles
        self.on_status = on_status
        self.on_progress = on_progress
        self.stop = threading.Event()

    def run(self):
        if self.titles:
            self.on_status("Feinvergleich: Titel gleicher Bands werden gesucht …")
            new = title_candidates(self.db)
            if new:
                self.db.add_title_pairs(new)
        jobs = self.db.pairs_to_compare(compare_mod.COMPARE_VERSION)
        if not jobs:
            self.on_status("Feinvergleich: nichts zu tun")
            return
        # Fortschritt ueber alle Paare: bereits verglichene zaehlen mit
        total = max(len(jobs), self.db.conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0])
        done = base = total - len(jobs)
        self.on_status(f"Feinvergleich: {len(jobs)} von {total} Paaren offen")
        self.on_progress(done, total)
        t0 = time.time()
        with ProcessPoolExecutor(max_workers=self.workers) as ex:
            pending = {}
            it = iter(jobs)

            def submit_next():
                try:
                    job = next(it)
                except StopIteration:
                    return False
                pending[ex.submit(compare_worker, job)] = job
                return True

            for _ in range(self.workers * 2):
                if not submit_next():
                    break
            while pending:
                finished, _ = wait(list(pending), timeout=0.5, return_when=FIRST_COMPLETED)
                for fut in finished:
                    job = pending.pop(fut)
                    try:
                        pid, kind, res, blob, err = fut.result()
                    except Exception as exc:
                        pid, kind, res, blob, err = job[0], "", None, None, str(exc)
                    self.db.store_compare(pid, kind, res, blob, compare_mod.COMPARE_VERSION, err)
                    done += 1
                    if not self.stop.is_set():
                        submit_next()
                if finished:
                    self.on_progress(done, total)
                    rate = (done - base) / max(1e-6, time.time() - t0)
                    self.on_status(f"Feinvergleich: {done} von {total} Paaren · "
                                   f"noch ca. {_fmt_dur((total - done) / max(rate, 1e-6))}")
                if self.stop.is_set():
                    for fut in pending:
                        fut.cancel()
                    ex.shutdown(wait=False, cancel_futures=True)
                    self.on_status("Feinvergleich angehalten – beim nächsten Start geht es hier weiter")
                    return
        self.on_status(f"Feinvergleich fertig: {total} Paare")
