"""Tabellenmodell der gefundenen Paare mit Sortierung und Filter."""
from __future__ import annotations

import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from . import theme
from .compare_view import fmt_time

COLUMNS = ["Band", "Titel A", "Titel B", "Album A", "Album B", "Art", "Gleich", "Länge A", "Länge B",
           "Ordner A", "Ordner B"]
COL_BAND, COL_TA, COL_TB, COL_AA, COL_AB, COL_KIND, COL_SIM, COL_LA, COL_LB, COL_FA, COL_FB = range(11)
SORT_ROLE = Qt.UserRole + 1
PAIR_ROLE = Qt.UserRole + 2


def _s(v) -> str:
    return (v or "").strip()


class PairModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.files: dict[int, dict] = {}

    def set_data(self, pairs: list[dict], files: dict[int, dict]):
        self.beginResetModel()
        self.files = files
        self.rows = [p for p in pairs if p["a_id"] in files and p["b_id"] in files]
        self.endResetModel()

    def merge(self, pairs: list[dict], files: dict[int, dict]) -> set[int]:
        """Neue Paare anhaengen, geaenderte aktualisieren -- ohne Modell-Reset, damit Auswahl,
        Scrollposition und ein laufendes A/B-Hoeren erhalten bleiben. Liefert die ids der neuen
        und geaenderten Paare."""
        self.files.update(files)
        pos = {p["id"]: i for i, p in enumerate(self.rows)}
        new, changed = [], set()
        for p in pairs:
            i = pos.get(p["id"])
            if i is None:
                if p["a_id"] in self.files and p["b_id"] in self.files:
                    new.append(p)
                continue
            row = self.rows[i]
            if any(row.get(k) != v for k, v in p.items()):
                row.update(p)     # dasselbe dict-Objekt -- Verweise darauf (aktuelles Paar) bleiben gueltig
                changed.add(p["id"])
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLUMNS) - 1))
        if new:
            self.beginInsertRows(QModelIndex(), len(self.rows), len(self.rows) + len(new) - 1)
            self.rows.extend(new)
            self.endInsertRows()
        return changed | {p["id"] for p in new}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return COLUMNS[section]
        return None

    def pair(self, row: int) -> dict:
        return self.rows[row]

    def remove_file(self, fid: int):
        keep = [p for p in self.rows if p["a_id"] != fid and p["b_id"] != fid]
        if len(keep) != len(self.rows):
            self.beginResetModel()
            self.rows = keep
            self.files.pop(fid, None)
            self.endResetModel()

    def update_pair(self, pair_id: int, **changes):
        for i, p in enumerate(self.rows):
            if p["id"] == pair_id:
                p.update(changes)
                self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLUMNS) - 1))
                return

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        p = self.rows[index.row()]
        a, b = self.files[p["a_id"]], self.files[p["b_id"]]
        c = index.column()
        if role == PAIR_ROLE:
            return p
        if role == Qt.DisplayRole:
            if c == COL_BAND:
                ba, bb = _s(a["artist"]), _s(b["artist"])
                return ba if ba.lower() == bb.lower() or not bb else f"{ba} / {bb}"
            if c == COL_TA:
                return _s(a["title"])
            if c == COL_TB:
                return _s(b["title"])
            if c == COL_AA:
                return _s(a["album"])
            if c == COL_AB:
                return _s(b["album"])
            if c == COL_KIND:
                text = theme.KIND_LABELS.get(p["kind"] if p["cmp_version"] else "", p["kind"])
                if p["variant"] and p["kind"] == "variante":
                    text += f" ({p['variant']})"
                return text
            if c == COL_SIM:
                return "…" if p["similarity"] is None else f"{p['similarity']:.0f} %"
            if c == COL_LA:
                return fmt_time(a["duration"])
            if c == COL_LB:
                return fmt_time(b["duration"])
            if c == COL_FA:
                return os.path.basename(a["folder"] or "")
            if c == COL_FB:
                return os.path.basename(b["folder"] or "")
        if role == SORT_ROLE:
            if c == COL_SIM:
                return -1.0 if p["similarity"] is None else float(p["similarity"])
            if c in (COL_LA, COL_LB):
                return float((a if c == COL_LA else b)["duration"] or 0)
            if c == COL_BAND:   # innerhalb der Band nach Titel
                return f"{_s(a['artist']).lower()}\x00{_s(a['title']).lower()}"
            if c in (COL_FA, COL_FB):   # ganzer Pfad, damit Ordner gleichen Namens zusammenstehen
                f = a if c == COL_FA else b
                return f"{(f['folder'] or '').lower()}\x00{(f['filename'] or '').lower()}"
            return (self.data(index, Qt.DisplayRole) or "").lower()
        if role == Qt.ToolTipRole:
            if c in (COL_TA, COL_AA, COL_FA, COL_LA):
                return a["path"]
            if c in (COL_TB, COL_AB, COL_FB, COL_LB):
                return b["path"]
            if c == COL_KIND:
                return theme.KIND_TIPS.get(p["kind"] if p["cmp_version"] else "", "")
            if c == COL_SIM and p["a_green"] is not None:
                return (f"Gleich klingend: A zu {100 * p['a_green']:.0f} %, B zu {100 * p['b_green']:.0f} %"
                        f"\nMedian-Übereinstimmung {p['sim_median']:.2f}")
        if role == Qt.ForegroundRole and c == COL_KIND:
            return theme.KIND_COLORS.get(p["kind"] if p["cmp_version"] else "", None)
        if role == Qt.TextAlignmentRole and c in (COL_SIM, COL_LA, COL_LB):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class PairFilter(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.kinds: set[str] | None = None
        self.review = "open"
        self.text = ""
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(False)

    def set_filter(self, kinds: set[str] | None, review: str, text: str):
        self.kinds, self.review, self.text = kinds, review, text.lower().strip()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        m: PairModel = self.sourceModel()
        p = m.rows[row]
        if self.review != "all" and p["review"] != self.review:
            return False
        kind = p["kind"] if p["cmp_version"] else ""
        if self.kinds is not None and kind not in self.kinds:
            return False
        if self.text:
            a, b = m.files[p["a_id"]], m.files[p["b_id"]]
            hay = " ".join(_s(x) for x in (a["path"], b["path"], a["artist"], b["artist"], a["title"],
                                           b["title"], a["album"], b["album"])).lower()
            if not all(word in hay for word in self.text.split()):
                return False
        return True
