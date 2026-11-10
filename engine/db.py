"""SQLite-Datenbank von FlacDup.

Phase 1 schreibt jede fertig analysierte Datei sofort weg (files + fingerprints);
Phase 2 merkt sich je Datei, ob sie schon abgeglichen ist (files.match_version).
Ein Absturz kostet daher hoechstens die gerade laufenden Dateien.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    folder TEXT, filename TEXT, ext TEXT,
    size INTEGER, mtime REAL,
    duration REAL, sr INTEGER,
    artist TEXT, title TEXT, album TEXT, year TEXT, track TEXT,
    status TEXT NOT NULL DEFAULT 'pending',      -- pending / done / error / missing
    error TEXT,
    fp_version INTEGER,
    match_version INTEGER,
    n_peaks INTEGER,
    preview BLOB,
    indexed_at REAL
);
CREATE INDEX IF NOT EXISTS files_status ON files(status);
CREATE TABLE IF NOT EXISTS fingerprints (
    file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE,
    peaks BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS pairs (
    id INTEGER PRIMARY KEY,
    a_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    b_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    votes INTEGER, density REAL, similarity REAL,
    kind TEXT,                     -- identisch / ausschnitt / variante
    variant TEXT,
    offset_s REAL, time_scale REAL, shift_bins REAL,   -- Zeit in B = Zeit in A * time_scale + offset
    a_start REAL, a_end REAL, b_start REAL, b_end REAL,
    review TEXT NOT NULL DEFAULT 'open',   -- open / not_dup / done
    source TEXT NOT NULL DEFAULT 'hash',   -- hash (Klangschluessel) / title (gleicher Titel)
    diff BLOB,                     -- Rot/Gruen-Verlauf aus dem Feinvergleich (Phase 3)
    cmp_version INTEGER,           -- NULL = Feinvergleich steht noch aus
    a_green REAL, b_green REAL, sim_median REAL,
    found_at REAL,
    UNIQUE(a_id, b_id)
);
CREATE INDEX IF NOT EXISTS pairs_b ON pairs(b_id);
CREATE TABLE IF NOT EXISTS scan_roots (path TEXT PRIMARY KEY, added_at REAL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

FILE_COLS = ("id", "path", "folder", "filename", "ext", "size", "mtime", "duration", "sr",
             "artist", "title", "album", "year", "track", "status", "error")


class Database:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.lock = threading.RLock()

    def _migrate(self):
        """Spalten nachtragen, die in aelteren Datenbanken fehlen."""
        added = {"pairs": ("source TEXT NOT NULL DEFAULT 'hash'", "cmp_version INTEGER", "a_green REAL",
                           "b_green REAL", "sim_median REAL")}
        for table, cols in added.items():
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for col in cols:
                if col.split()[0] not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
        self.conn.commit()

    def close(self):
        with self.lock:
            self.conn.close()

    # ------------------------------------------------------------ Einstellungen
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str):
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, value))

    # ------------------------------------------------------------ Phase 1
    def add_root(self, path: str):
        with self.lock, self.conn:
            self.conn.execute("INSERT OR IGNORE INTO scan_roots VALUES (?,?)", (path, time.time()))

    def roots(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT path FROM scan_roots ORDER BY path")]

    def file_states(self) -> dict[str, tuple[int, int, float, str, int]]:
        """path -> (id, size, mtime, status, fp_version)"""
        return {r[0]: r[1:] for r in self.conn.execute(
            "SELECT path, id, size, mtime, status, fp_version FROM files")}

    def register(self, files: list[tuple[str, int, float]]):
        """Neue oder veraenderte Dateien als 'pending' eintragen (Phase 1 beginnt mit der Liste)."""
        with self.lock, self.conn:
            for path, size, mtime in files:
                folder, filename = os.path.split(path)
                self.conn.execute(
                    "INSERT INTO files(path, folder, filename, ext, size, mtime, status) "
                    "VALUES (?,?,?,?,?,?,'pending') ON CONFLICT(path) DO UPDATE SET "
                    "size=excluded.size, mtime=excluded.mtime, status='pending', match_version=NULL",
                    (path, folder, filename, os.path.splitext(filename)[1].lower().lstrip("."), size, mtime))

    def store_fingerprint(self, res: dict, fp_version: int, tags: dict):
        with self.lock, self.conn:
            fid = self.conn.execute("SELECT id FROM files WHERE path=?", (res["path"],)).fetchone()
            if fid is None:
                return None
            fid = fid[0]
            # Neuer Fingerabdruck: alte Paare dieser Datei sind ungueltig
            self.conn.execute("DELETE FROM pairs WHERE (a_id=? OR b_id=?) AND review='open'", (fid, fid))
            if res.get("ok"):
                self.conn.execute(
                    "UPDATE files SET status='done', error=NULL, fp_version=?, match_version=NULL, "
                    "duration=?, sr=?, n_peaks=?, preview=?, indexed_at=?, "
                    "artist=?, title=?, album=?, year=?, track=? WHERE id=?",
                    (fp_version, res["duration"], res["sr"], res["n_peaks"], res["preview"], time.time(),
                     tags.get("artist"), tags.get("title"), tags.get("album"), tags.get("year"),
                     tags.get("track"), fid))
                self.conn.execute("INSERT OR REPLACE INTO fingerprints VALUES (?,?)", (fid, res["peaks"]))
            else:
                self.conn.execute(
                    "UPDATE files SET status='error', error=?, fp_version=?, duration=?, indexed_at=?, "
                    "artist=?, title=?, album=?, year=?, track=? WHERE id=?",
                    (res.get("error"), fp_version, res.get("duration"), time.time(),
                     tags.get("artist"), tags.get("title"), tags.get("album"), tags.get("year"),
                     tags.get("track"), fid))
                self.conn.execute("DELETE FROM fingerprints WHERE file_id=?", (fid,))
            return fid

    def mark_missing(self, ids: list[int]):
        with self.lock, self.conn:
            self.conn.executemany("UPDATE files SET status='missing' WHERE id=?", [(i,) for i in ids])

    def unreadable(self) -> list[tuple[int, str, str]]:
        """(id, Pfad, Grund) der Dateien, die sich nicht lesen liessen."""
        return self.conn.execute("SELECT id, path, COALESCE(error, '') FROM files WHERE status='error' "
                                 "ORDER BY path").fetchall()

    def retry(self, ids: list[int]):
        """Beim naechsten Lauf erneut einlesen (Phase 1)."""
        with self.lock, self.conn:
            self.conn.executemany("UPDATE files SET status='pending', error=NULL, fp_version=NULL, "
                                  "match_version=NULL WHERE id=?", [(i,) for i in ids])

    def relocate(self, fid: int, path: str, size: int, mtime: float):
        folder, filename = os.path.split(path)
        with self.lock, self.conn:
            self.conn.execute("UPDATE files SET path=?, folder=?, filename=?, size=?, mtime=?, "
                              "status=CASE WHEN fp_version IS NULL THEN 'pending' ELSE 'done' END "
                              "WHERE id=?", (path, folder, filename, size, mtime, fid))

    # ------------------------------------------------------------ Phase 2
    def iter_fingerprints(self, batch: int = 2000):
        """(file_id, peaks-blob, match_version) aller fertigen Dateien."""
        cur = self.conn.execute(
            "SELECT f.id, p.peaks, f.match_version FROM files f JOIN fingerprints p ON p.file_id=f.id "
            "WHERE f.status='done' ORDER BY f.id")
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                return
            yield from rows

    def store_matches(self, query_id: int, rows: list[dict], match_version: int):
        """Paare einer Abfrage speichern und die Abfrage als abgeglichen markieren -- in einem Rutsch."""
        with self.lock, self.conn:
            for r in rows:
                a, b = r["a_id"], r["b_id"]
                old = self.conn.execute("SELECT id, votes, review FROM pairs WHERE a_id=? AND b_id=?",
                                        (a, b)).fetchone()
                vals = (r["votes"], r["density"], r["similarity"], r["kind"], r["variant"], r["offset_s"],
                        r["time_scale"], r["shift_bins"], r["a_start"], r["a_end"], r["b_start"], r["b_end"])
                if old is None:
                    self.conn.execute(
                        "INSERT INTO pairs(votes, density, similarity, kind, variant, offset_s, time_scale, "
                        "shift_bins, a_start, a_end, b_start, b_end, a_id, b_id, found_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals + (a, b, time.time()))
                elif r["votes"] > (old[1] or 0):
                    self.conn.execute(
                        "UPDATE pairs SET votes=?, density=?, similarity=?, kind=?, variant=?, offset_s=?, "
                        "time_scale=?, shift_bins=?, a_start=?, a_end=?, b_start=?, b_end=?, diff=NULL, "
                        "cmp_version=NULL, source='hash' WHERE id=?", vals + (old[0],))
            self.conn.execute("UPDATE files SET match_version=? WHERE id=?", (match_version, query_id))

    def title_groups(self) -> list[tuple[int, str, str, float]]:
        """(id, Band, Titel, Dauer) aller fertigen Dateien fuer den Titelvergleich."""
        return self.conn.execute(
            "SELECT id, artist, title, duration FROM files WHERE status='done'").fetchall()

    def existing_pairs(self) -> set[tuple[int, int]]:
        return set(self.conn.execute("SELECT a_id, b_id FROM pairs").fetchall())

    def add_title_pairs(self, pairs: list[tuple[int, int]]):
        with self.lock, self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO pairs(a_id, b_id, votes, kind, variant, time_scale, shift_bins, "
                "source, found_at) VALUES (?,?,0,'version','',1.0,0.0,'title',?)",
                [(a, b, time.time()) for a, b in pairs])

    def pairs_to_compare(self, cmp_version: int) -> list[tuple]:
        return self.conn.execute(
            "SELECT p.id, a.path, b.path, p.time_scale, p.offset_s, p.shift_bins, p.variant, p.source "
            "FROM pairs p JOIN files a ON a.id=p.a_id JOIN files b ON b.id=p.b_id "
            "WHERE (p.cmp_version IS NULL OR p.cmp_version<>?) AND a.status='done' AND b.status='done' "
            "ORDER BY p.id", (cmp_version,)).fetchall()

    def store_compare(self, pair_id: int, kind: str, res: dict | None, blob: bytes | None,
                      cmp_version: int, error: str | None = None):
        with self.lock, self.conn:
            if res is None:  # Datei nicht lesbar: als erledigt markieren, Art bleibt
                self.conn.execute("UPDATE pairs SET cmp_version=?, kind=COALESCE(kind,'?') WHERE id=?",
                                  (cmp_version, pair_id))
                return
            self.conn.execute(
                "UPDATE pairs SET cmp_version=?, kind=?, similarity=?, a_green=?, b_green=?, sim_median=?, "
                "diff=?, offset_s=COALESCE(offset_s, ?) WHERE id=?",
                (cmp_version, kind, res["similarity"], res["a_green"], res["b_green"], res["median"], blob,
                 res.get("offset_used"), pair_id))

    def reset_matching(self):
        with self.lock, self.conn:
            self.conn.execute("UPDATE files SET match_version=NULL")
            self.conn.execute("DELETE FROM pairs WHERE review='open'")

    # ------------------------------------------------------------ Liste / Pruefung
    def counts(self) -> dict:
        c = dict(self.conn.execute("SELECT status, COUNT(*) FROM files GROUP BY status").fetchall())
        c["unmatched"] = self.conn.execute(
            "SELECT COUNT(*) FROM files WHERE status='done' AND match_version IS NULL").fetchone()[0]
        c["pairs"] = self.conn.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
        c["uncompared"] = self.conn.execute("SELECT COUNT(*) FROM pairs WHERE cmp_version IS NULL").fetchone()[0]
        c["open"] = self.conn.execute("SELECT COUNT(*) FROM pairs WHERE review='open'").fetchone()[0]
        return c

    def load_pairs(self) -> list[dict]:
        cols = ("id", "a_id", "b_id", "votes", "density", "similarity", "kind", "variant", "offset_s",
                "time_scale", "shift_bins", "a_start", "a_end", "b_start", "b_end", "review",
                "source", "cmp_version", "a_green", "b_green", "sim_median")
        rows = self.conn.execute(f"SELECT {', '.join(cols)} FROM pairs").fetchall()
        return [dict(zip(cols, r)) for r in rows]

    def load_files(self, ids) -> dict[int, dict]:
        ids = list(ids)
        out = {}
        for k in range(0, len(ids), 900):
            part = ids[k:k + 900]
            q = f"SELECT {', '.join(FILE_COLS)} FROM files WHERE id IN ({','.join('?' * len(part))})"
            for r in self.conn.execute(q, part):
                out[r[0]] = dict(zip(FILE_COLS, r))
        return out

    def preview(self, fid: int) -> bytes | None:
        row = self.conn.execute("SELECT preview FROM files WHERE id=?", (fid,)).fetchone()
        return row[0] if row else None

    def set_review(self, pair_ids: list[int], review: str):
        with self.lock, self.conn:
            self.conn.executemany("UPDATE pairs SET review=? WHERE id=?", [(review, i) for i in pair_ids])

    def get_diff(self, pair_id: int) -> bytes | None:
        row = self.conn.execute("SELECT diff FROM pairs WHERE id=?", (pair_id,)).fetchone()
        return row[0] if row else None

    def set_diff(self, pair_id: int, blob: bytes):
        with self.lock, self.conn:
            self.conn.execute("UPDATE pairs SET diff=? WHERE id=?", (blob, pair_id))

    def file_removed(self, fid: int, new_path: str | None = None):
        """Datei wurde geloescht (new_path None) oder verschoben."""
        with self.lock, self.conn:
            if new_path is None:
                self.conn.execute("DELETE FROM files WHERE id=?", (fid,))
            else:
                folder, filename = os.path.split(new_path)
                self.conn.execute("UPDATE files SET path=?, folder=?, filename=? WHERE id=?",
                                  (new_path, folder, filename, fid))
