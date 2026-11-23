from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QSizePolicy, QSplitter, QTableView, QVBoxLayout, QWidget)
from send2trash import send2trash

from engine import compare, editing, recycle
from engine.cover import cover_bytes
from engine.db import Database
from engine.decode import AUDIO_EXTENSIONS, probe
from engine.pipeline import Comparer, Indexer, Matcher, classify_compare, default_workers, reevaluate
from engine.tags import read_tags
from lang import LANG, LANGS, T, set_language
from version import APP_NAME, VERSION

from . import theme
from .ab_player import ABPlayer
from .check_combo import CheckCombo
from .compare_view import CompareView, fmt_time
from .pair_model import COL_BAND, PAIR_ROLE, MismatchDelegate, PairFilter, PairModel
from .widgets import BLUE_BUTTON, GREEN_BUTTON, TrashButton

APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
ERROR_LOG = APP_DATA_DIR / "fehler.log"
DEFAULT_DB_PATH = APP_DATA_DIR / "flacdup.db"
CONFIG_PATH = APP_DATA_DIR / "config.json"

KIND_ORDER = ["identisch", "ausschnitt", "variante", "teilweise", "version", "schwach", ""]
DEFAULT_KINDS_OFF = {"schwach"}
REVIEW_CHOICES = [(T("Offen"), "open"), (T("Kein Duplikat"), "not_dup"), (T("Alle"), "all")]
UNDO_LIMIT = 50
# Spalten der Dateiinfo ueber der Welle
# Spalten der Dateiinfo: Seite, Ordner, Dateiname, Dateien im Ordner, Länge, Abtastrate, Bitrate, Packrate,
# Kanäle, gleich klingend -- Ordner und Dateiname links, die Zahlen rechtsbuendig
INFO_COLS = 10
INFO_LEFT = (0, 1, 2)
PHASES = {"p1": T("Phase 1 von 3: Index"), "p2": T("Phase 2 von 3: Abgleich"), "p3": T("Phase 3 von 3: Feinvergleich")}


def log_error(where: str, exc: BaseException):
    """Fehler mit Stacktrace in fehler.log schreiben (FlacDup laeuft ohne Konsole)."""
    import traceback
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(ERROR_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"\n=== {_dt.datetime.now():%Y-%m-%d %H:%M:%S} · {where} · {APP_NAME} {VERSION}\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        pass


def move_file(src: str, dst: str):
    """Datei verschieben/umbenennen; kurz erneut versuchen, falls Windows sie noch gesperrt haelt
    (Virenscanner, Vorschau). Auf demselben Laufwerk nie Kopieren+Loeschen: das liesse bei einer
    Sperre zwei Dateien zurueck."""
    import time
    same = os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(src))
    if os.path.exists(dst) and not same:
        raise FileExistsError(T("Am Ziel liegt schon eine Datei:\n{path}", path=dst))
    one_drive = os.path.splitdrive(os.path.abspath(src))[0].lower() == os.path.splitdrive(os.path.abspath(dst))[0].lower()
    for attempt in range(20):
        try:
            os.replace(src, dst) if one_drive else shutil.move(src, dst)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.25)


def _read_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_config(cfg: dict):
    try:
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        log_error("config schreiben", exc)


class Bridge(QObject):
    """Signale aus Hintergrund-Threads in den GUI-Thread."""
    status = Signal(str)
    progress = Signal(int, int)
    phase = Signal(str)
    finished = Signal(str)
    compared = Signal(int, object)     # pair_id, Ergebnis (oder None)
    reevaluated = Signal(object, object)   # Kontext des Editors, [(Paar-id, Uebereinstimmung)]


class MainWindow(QMainWindow):
    def __init__(self, db_path: str | None = None):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {VERSION}")
        self.resize(1500, 950)
        cfg = _read_config()
        path = db_path or cfg.get("db") or str(DEFAULT_DB_PATH)
        self.db = Database(path)
        self.bridge = Bridge()
        self.bridge.status.connect(self._on_status)
        self.bridge.progress.connect(self._on_progress)
        self.bridge.phase.connect(self._on_phase)
        self.bridge.finished.connect(self._on_finished)
        self.bridge.compared.connect(self._on_compared)
        self.bridge.reevaluated.connect(self._on_reevaluated)
        self.worker: threading.Thread | None = None
        self.job = None
        self.current: dict | None = None
        self._computing: set[int] = set()
        self._undo: list[tuple[str, object]] = []    # (Beschreibung, Funktion) -- Strg+Z
        self._reeval_busy = False
        self._covers: dict[str, bytes | None] = {}   # Pfad -> Coverbild
        self._pack_cache: dict = {}        # (Pfad, Groesse) -> Packrate-Text
        self._cur_cmp = False              # Rot/Gruen des aktuellen Paares liegt vor
        # Funde aus Phase 2/3 laufend in die Liste holen (jedes Paar ist sofort gespeichert)
        self.live_timer = QTimer(self)
        self.live_timer.setInterval(15000)
        self.live_timer.timeout.connect(self._live_reload)

        self.player = ABPlayer(self)
        self.player.loaded.connect(self._on_loaded)
        self.player.load_failed.connect(lambda pid, msg: self.view.clear(T("Datei nicht lesbar: {err}", err=msg)))
        self.player.position_changed.connect(self._on_position)
        self.player.state_changed.connect(self._on_play_state)
        self.player.side_changed.connect(self._on_side)
        self.player.error.connect(lambda m: QMessageBox.warning(self, APP_NAME, m))

        from .update_check import UpdateChecker
        self.updates = UpdateChecker(_read_config, _write_config, self)
        self.updates.found.connect(self._on_update_found)
        self._new_version = ""
        self._build_ui()
        self._build_menu()
        QTimer.singleShot(3000, self.updates.start)
        self._shortcuts()
        self.setAcceptDrops(True)
        self.reload()

    # ------------------------------------------------------------ Aufbau
    def _build_ui(self):
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        self.btn_add = QPushButton(T("Ordner hinzufügen …"))
        self.btn_add.setToolTip(T("Ordner wählen und analysieren (auch per Drag & Drop ins Fenster)"))
        self.btn_add.clicked.connect(self._add_folder)
        self.btn_run = QPushButton("▶  " + T("Analyse fortsetzen"))
        self.btn_run.setToolTip(T("Alle bekannten Ordner aktualisieren und die drei Phasen abarbeiten:\n"
                                  "1 Index (Fingerabdruck je Datei) · 2 Abgleich · 3 Feinvergleich.\n"
                                  "Nach einem Abbruch oder Absturz geht es dort weiter, wo es aufgehört hat."))
        self.btn_run.clicked.connect(lambda: self._start_job(self.db.roots()))
        self.btn_stop = QPushButton("■  " + T("Anhalten"))
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_job)
        self.chk_var = QCheckBox(T("Tempo/Tonhöhe"))
        self.chk_var.setToolTip(T("Auch schneller/langsamer abgespielte und in der Tonhöhe verschobene Fassungen "
                                  "suchen (±10 %).\nDer Abgleich dauert damit etwa 20-mal so lange."))
        self.chk_var.setChecked(self.db.get_setting("variants", "1") == "1")
        self.chk_var.toggled.connect(lambda on: self.db.set_setting("variants", "1" if on else "0"))
        self.chk_titles = QCheckBox(T("Gleiche Titel"))
        self.chk_titles.setToolTip(T("Dateien gleicher Band mit gleichem Titel zusätzlich direkt vergleichen.\n"
                                     "Findet andere Aufnahmen (live, neu eingespielt) und alte Aufnahmen,\n"
                                     "deren Klangschlüssel nicht greifen."))
        self.chk_titles.setChecked(self.db.get_setting("titles", "1") == "1")
        self.chk_titles.toggled.connect(lambda on: self.db.set_setting("titles", "1" if on else "0"))
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setTextVisible(True)
        self.lbl_status = QLabel("")
        # gruener Knopf, sobald es eine neuere Version gibt (Update-Pruefung)
        self.btn_update = QPushButton("")
        self.btn_update.setStyleSheet("QPushButton { background: #2f7d46; color: white; font-weight: bold; "
                                      "padding: 3px 10px; }")
        self.btn_update.setVisible(False)
        self.btn_update.clicked.connect(self._update_menu)
        for w in (self.btn_add, self.btn_run, self.btn_stop, self.chk_var, self.chk_titles):
            top.addWidget(w)
        top.addSpacing(12)
        top.addWidget(self.progress)
        top.addWidget(self.lbl_status, 1)
        top.addWidget(self.btn_update)
        lay.addLayout(top)

        filt = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(T("Suchen (Band, Titel, Album, Pfad) …"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda: self._filter_timer.start())
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self._apply_filter)
        self.kind_combo = CheckCombo(T("Alle Arten"), T("Arten"))
        self.kind_combo.set_items([(theme.KIND_LABELS[k], k) for k in KIND_ORDER],
                                  set(KIND_ORDER) - self._kinds_off())
        self.kind_combo.selection_changed.connect(self._on_kinds_changed)
        self.review_combo = QComboBox()
        for label, val in REVIEW_CHOICES:
            self.review_combo.addItem(label, val)
        self.review_combo.currentIndexChanged.connect(self._apply_filter)
        self.lbl_count = QLabel("")
        filt.addWidget(self.search, 2)
        filt.addWidget(QLabel(T("Art:")))
        filt.addWidget(self.kind_combo)
        filt.addWidget(QLabel(T("Status:")))
        filt.addWidget(self.review_combo)
        filt.addWidget(self.lbl_count, 1)
        lay.addLayout(filt)

        self.model = PairModel(self)
        self.proxy = PairFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setItemDelegate(MismatchDelegate(self.table))
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(22)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for col, w in enumerate((170, 240, 240, 170, 170, 150, 60, 60, 60, 220, 220)):
            self.table.setColumnWidth(col, w)
        self.table.sortByColumn(COL_BAND, Qt.AscendingOrder)
        self.table.selectionModel().currentRowChanged.connect(self._on_row)

        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 4, 0, 0)
        # Dateiinfo A/B als Tabelle: gleiche Angaben stehen untereinander
        info = QGridLayout()
        info.setHorizontalSpacing(14)
        info.setVerticalSpacing(2)
        self.info_cells: list[list[QLabel]] = []
        for r, side in enumerate("AB"):
            row = []
            for c in range(INFO_COLS):
                lbl = QLabel("")
                lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                if c == 0:
                    lbl.setText(f"<b>{side}</b>")
                if c in (1, 2):     # lange Pfade/Namen duerfen abgeschnitten werden, voller Text im Tooltip
                    lbl.setMinimumWidth(40)
                    lbl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                info.addWidget(lbl, r, c, Qt.AlignLeft if c in INFO_LEFT else Qt.AlignRight)
                row.append(lbl)
            self.info_cells.append(row)
        info.setColumnStretch(1, 3)
        info.setColumnStretch(2, 2)
        dl.addLayout(info)

        # Links neben der Welle: rote Papierkorb-Symbole auf Hoehe von A und B, darunter gross und blau
        # "Kein Duplikat"; rechts daneben, direkt unter der Welle, Play/Pause, A/B und die Ordner.
        self.view = CompareView()
        self.view.seek_requested.connect(self._seek)
        self.view.edit_requested.connect(self._open_editor)
        self.btn_trash_a = TrashButton()
        self.btn_trash_b = TrashButton()
        for side, btn in enumerate((self.btn_trash_a, self.btn_trash_b)):
            btn.setToolTip(T("Datei {side} in den Windows-Papierkorb legen (Strg+Z holt sie zurück)", side="AB"[side]))
            btn.clicked.connect(lambda _=False, s=side: self._trash(s))
            btn.hovered.connect(lambda on, s=side: self.view.set_trash_hint(s if on else None))
        self.btn_notdup = QPushButton(T("Kein Duplikat"))
        self.btn_notdup.setToolTip(T("Beide Dateien behalten – das Paar verschwindet aus der Liste „Offen“ "
                                     "und taucht nicht wieder auf (Taste N, Strg+Z macht es rückgängig)"))
        self.btn_notdup.clicked.connect(lambda: self._set_review("not_dup"))
        self.btn_reopen = QPushButton(T("Wieder in die Liste"))
        self.btn_reopen.setToolTip(T("Dieses Paar war als „Kein Duplikat“ markiert – zurück in die Liste „Offen“ holen"))
        self.btn_reopen.clicked.connect(lambda: self._set_review("open"))
        for btn in (self.btn_notdup, self.btn_reopen):
            btn.setMinimumHeight(66)
            btn.setStyleSheet(BLUE_BUTTON)
        side_w = max(118, max(b.sizeHint().width() for b in (self.btn_notdup, self.btn_reopen)) + 16)
        side_col = QVBoxLayout()
        side_col.setContentsMargins(0, 0, 0, 0)
        side_col.addStretch(1)
        side_col.addWidget(self.btn_trash_a, 0, Qt.AlignHCenter)
        side_col.addStretch(2)
        side_col.addWidget(self.btn_trash_b, 0, Qt.AlignHCenter)
        side_col.addStretch(1)

        self.btn_play = QPushButton("▶  " + T("Abspielen"))
        self.btn_play.setToolTip(T("Abspielen / Pause (Leertaste)"))
        self.btn_play.clicked.connect(self.player.toggle)
        self.btn_play.setMinimumSize(150, 66)
        self.btn_play.setStyleSheet(GREEN_BUTTON)
        self.btn_a = QPushButton("A")
        self.btn_b = QPushButton("B")
        for k, btn in enumerate((self.btn_a, self.btn_b)):
            btn.setCheckable(True)
            btn.setStyleSheet("QPushButton:checked { background: #3c6ea0; font-weight: bold; }")
            btn.clicked.connect(lambda _=False, s=k: self._choose_side(s))
        for s, btn in (("A", self.btn_a), ("B", self.btn_b)):
            btn.setToolTip(T("Taste {side} – an der entsprechenden Stelle auf {side} umschalten (Tab wechselt)", side=s))
        self.btn_dir_a = QPushButton(T("Ordner von {side}", side="A"))
        self.btn_dir_b = QPushButton(T("Ordner von {side}", side="B"))
        self.btn_dir_a.setToolTip(T("Ordner von {side} im Explorer öffnen, die Datei ist markiert", side="A"))
        self.btn_dir_b.setToolTip(T("Ordner von {side} im Explorer öffnen, die Datei ist markiert", side="B"))
        self.btn_dir_a.clicked.connect(lambda: self._open_dir(0))
        self.btn_dir_b.clicked.connect(lambda: self._open_dir(1))
        self.lbl_pos = QLabel("")
        self.lbl_pos.setMinimumWidth(260)
        hint = QLabel(T("Leertaste = Abspielen/Pause · Tab = A/B umschalten · Klick in die Welle = springen · "
                        "Mausrad = Zoom · Doppelklick = alles · Rechtsklick = bearbeiten · Strg+Z = rückgängig"))
        hint.setStyleSheet("color: #8a8a8a;")
        hint.setWordWrap(True)
        transport = QGridLayout()
        transport.setHorizontalSpacing(6)
        transport.addWidget(self.btn_play, 0, 0, 2, 1)
        transport.addWidget(self.btn_a, 0, 1)
        transport.addWidget(self.btn_b, 0, 2)
        transport.addWidget(self.btn_dir_a, 1, 1)
        transport.addWidget(self.btn_dir_b, 1, 2)
        transport.addWidget(self.lbl_pos, 0, 3)
        transport.addWidget(hint, 0, 4, 2, 1)
        transport.setColumnStretch(4, 1)

        body = QGridLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setHorizontalSpacing(8)
        body.setColumnMinimumWidth(0, side_w)
        body.addLayout(side_col, 0, 0)
        body.addWidget(self.view, 0, 1)
        notdup_box = QVBoxLayout()
        notdup_box.addWidget(self.btn_notdup)
        notdup_box.addWidget(self.btn_reopen)
        body.addLayout(notdup_box, 1, 0)
        body.addLayout(transport, 1, 1)
        body.setRowStretch(0, 1)
        body.setColumnStretch(1, 1)
        dl.addLayout(body, 1)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)
        self.setCentralWidget(central)
        self._enable_detail(False)

    def _build_menu(self):
        m = self.menuBar().addMenu(T("&Datei"))
        a = QAction(T("Ordner hinzufügen …"), self)
        a.triggered.connect(self._add_folder)
        m.addAction(a)
        m.addSeparator()
        a = QAction(T("Datenbank öffnen …"), self)
        a.triggered.connect(self._open_db)
        m.addAction(a)
        a = QAction(T("Neue Datenbank …"), self)
        a.triggered.connect(self._new_db)
        m.addAction(a)
        m.addSeparator()
        a = QAction(T("Nicht lesbare Dateien …"), self)
        a.triggered.connect(self._show_unreadable)
        m.addAction(a)
        a = QAction(T("Abgleich und Feinvergleich neu berechnen"), self)
        a.setToolTip(T("Index bleibt erhalten; offene Paare werden neu gesucht (Prüfstand bleibt)."))
        a.triggered.connect(self._reset_matching)
        m.addAction(a)
        m.addSeparator()
        a = QAction(T("Beenden"), self)
        a.triggered.connect(self.close)
        m.addAction(a)
        e = self.menuBar().addMenu(T("&Bearbeiten"))
        self.act_undo = QAction(T("Rückgängig"), self)
        self.act_undo.setShortcut(QKeySequence.Undo)
        self.act_undo.setEnabled(False)
        self.act_undo.triggered.connect(self._do_undo)
        e.addAction(self.act_undo)
        h = self.menuBar().addMenu(T("&Hilfe"))
        a = QAction(T("Über {app} …", app=APP_NAME), self)
        a.triggered.connect(self._about)
        h.addAction(a)
        h.addSeparator()
        lm = h.addMenu("Sprache · Language · Langue")
        for code, name in LANGS.items():
            a = QAction(name, self)
            a.setCheckable(True)
            a.setChecked(code == LANG)
            a.triggered.connect(lambda _=False, c=code: self._choose_language(c))
            lm.addAction(a)
        self.act_updates = QAction(T("Nach Updates suchen"), self)
        self.act_updates.setCheckable(True)
        self.act_updates.setChecked(self.updates.enabled())
        self.act_updates.setToolTip(T("Einmal am Tag auf GitHub nachsehen, ob es eine neue Version gibt"))
        self.act_updates.toggled.connect(self._toggle_updates)
        h.addAction(self.act_updates)

    def _show_unreadable(self):
        from .unreadable_dialog import UnreadableDialog
        dlg = UnreadableDialog(self.db, self)
        dlg.exec()
        if dlg.retried and self.worker is None:
            self._refresh_status()

    def _shortcuts(self):
        def sc(key, fn):
            s = QShortcut(QKeySequence(key), self)
            s.setContext(Qt.WindowShortcut)
            s.activated.connect(fn)
        sc(Qt.Key_Space, self.player.toggle)
        sc(Qt.Key_Tab, lambda: self.player.switch())
        sc(Qt.Key_A, lambda: self._choose_side(0))
        sc(Qt.Key_B, lambda: self._choose_side(1))
        sc(Qt.Key_N, lambda: self._set_review("not_dup"))
        sc(Qt.Key_Left, lambda: self._nudge(-5.0))
        sc(Qt.Key_Right, lambda: self._nudge(5.0))

    # ------------------------------------------------------------ Daten
    def _kinds_off(self) -> set[str]:
        raw = self.db.get_setting("kinds_off")
        return set(json.loads(raw)) if raw else set(DEFAULT_KINDS_OFF)

    def _on_kinds_changed(self):
        off = set(KIND_ORDER) - set(self.kind_combo.checked_values())
        self.db.set_setting("kinds_off", json.dumps(sorted(off)))
        self._apply_filter()

    def reload(self, keep_pair: int | None = None):
        pairs = self.db.load_pairs()
        files = self.db.load_files({p["a_id"] for p in pairs} | {p["b_id"] for p in pairs})
        self.model.set_data(pairs, files)
        self._apply_filter(keep_pair)
        self._refresh_status()

    def _refresh_status(self):
        c = self.db.counts()
        roots = self.db.roots()
        self.setWindowTitle(f"{APP_NAME} {VERSION} – {self.db.path}")
        if not self.worker:
            todo = []
            if c.get("pending"):
                todo.append(T("{n} Dateien ohne Fingerabdruck", n=c["pending"]))
            if c.get("unmatched"):
                todo.append(T("{n} nicht abgeglichen", n=c["unmatched"]))
            if c.get("uncompared"):
                todo.append(T("{n} Paare ohne Feinvergleich", n=c["uncompared"]))
            base = (T("{files} Dateien im Index, {pairs} Paare", files=c.get("done", 0), pairs=c.get("pairs", 0))
                    + (" · " + T("{n} nicht lesbar", n=c["error"]) if c.get("error") else ""))
            self.lbl_status.setText(base + (" · " + T("offen:") + " " + ", ".join(todo) if todo else "")
                                    if roots else T("Noch keine Ordner – „Ordner hinzufügen …“ "
                                                    "oder Ordner ins Fenster ziehen"))
        self.btn_run.setEnabled(bool(roots) and self.worker is None)

    def _live_reload(self):
        """Neue und fertig verglichene Paare waehrend der Analyse nachtragen, ohne Reset."""
        try:
            pairs = self.db.load_pairs()
            need = ({p["a_id"] for p in pairs} | {p["b_id"] for p in pairs}) - self.model.files.keys()
            changed = self.model.merge(pairs, self.db.load_files(need) if need else {})
        except Exception as exc:
            log_error("Liste nachladen", exc)
            return
        if not changed:
            return
        self._apply_filter(reselect=False)    # neu filtern/sortieren (Layout-Aenderung, kein Reset)
        p = self.current
        if p is not None and p["id"] in changed:
            p.update(next(r for r in self.model.rows if r["id"] == p["id"]))   # self.current ist eine Kopie
            a, b = self.model.files[p["a_id"]], self.model.files[p["b_id"]]
            self._show_info(p, (a, b))
            if not self._cur_cmp and p["cmp_version"]:
                blob = self.db.get_diff(p["id"])
                res = compare.unpack(blob) if blob else None
                if res is not None:
                    self._cur_cmp = True
                    self.view.set_compare(res)
                    self.player.set_compare(res)
                    self.view.off = res["offset_used"]

    def _apply_filter(self, keep_pair: int | None = None, reselect: bool = True):
        cur = keep_pair if keep_pair is not None else (self.current or {}).get("id")
        if not reselect:
            cur = None             # Auswahl bleibt ueber die Layout-Aenderung erhalten, Liste springt nicht
        kinds = set(self.kind_combo.checked_values())
        self.proxy.set_filter(None if kinds == set(KIND_ORDER) else kinds,
                              self.review_combo.currentData(), self.search.text())
        self.proxy.sort(self.table.horizontalHeader().sortIndicatorSection(),
                        self.table.horizontalHeader().sortIndicatorOrder())
        n = self.proxy.rowCount()
        self.lbl_count.setText(T("{n} von {total} Paaren", n=n, total=self.model.rowCount()))
        if cur is not None:
            for r in range(n):
                if self.proxy.index(r, 0).data(PAIR_ROLE)["id"] == cur:
                    self.table.selectRow(r)
                    return

    # ------------------------------------------------------------ Phasen
    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, T("Ordner mit Musik wählen"),
                                             self.db.get_setting("last_dir", "") or "")
        if d:
            self.db.set_setting("last_dir", d)
            self._start_job([os.path.normpath(d)])

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        paths = [os.path.normpath(u.toLocalFile()) for u in ev.mimeData().urls() if u.isLocalFile()]
        paths = [p for p in paths if os.path.isdir(p) or os.path.splitext(p)[1].lower() in AUDIO_EXTENSIONS]
        if paths:
            self._start_job(paths)

    def _start_job(self, targets: list[str]):
        if self.worker is not None:
            QMessageBox.information(self, APP_NAME, T("Es läuft bereits eine Analyse."))
            return
        targets = list(dict.fromkeys(targets))
        b = self.bridge
        workers = default_workers()
        variants, titles = self.chk_var.isChecked(), self.chk_titles.isChecked()

        def run():
            msg = "fertig"
            try:
                for name, make in (
                        ("p1", lambda: Indexer(self.db, targets, workers, b.status.emit,
                                                                 b.progress.emit)),
                        ("p2", lambda: Matcher(self.db, variants, b.status.emit,
                                                                    b.progress.emit)),
                        ("p3", lambda: Comparer(self.db, workers, titles,
                                                                          b.status.emit, b.progress.emit))):
                    b.phase.emit(name)
                    self.job = make()
                    self.job.run()
                    if self.job.stop.is_set():
                        msg = "angehalten"
                        break
                    b.phase.emit("reload")
            except Exception as exc:
                log_error("Analyse", exc)
                msg = f"Fehler: {exc}"
            b.finished.emit(msg)

        self.worker = threading.Thread(target=run, daemon=True)
        self.btn_stop.setEnabled(True)
        self.btn_run.setEnabled(False)
        self.btn_add.setEnabled(False)
        self.worker.start()

    def _stop_job(self):
        if self.job is not None:
            self.job.stop.set()
            self.lbl_status.setText(T("Wird angehalten …"))

    def _on_status(self, text: str):
        self.lbl_status.setText(text)

    def _on_progress(self, done: int, total: int):
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(done)

    def _on_phase(self, name: str):
        if name == "reload":
            self._live_reload()
        else:
            self.progress.setFormat(PHASES[name].split(":")[0] + ": %p %")
            self.progress.setToolTip(PHASES[name])
            self.progress.setValue(0)
            if name in ("p2", "p3"):
                self.live_timer.start()

    def _on_finished(self, msg: str):
        self.live_timer.stop()
        self.worker = None
        self.job = None
        self.btn_stop.setEnabled(False)
        self.btn_add.setEnabled(True)
        self.progress.setFormat("%p %")
        self._live_reload()        # kein Reset: ein gerade gehoertes Paar spielt weiter
        self._refresh_status()
        if msg.startswith("Fehler"):
            QMessageBox.warning(self, APP_NAME, T("Analyse abgebrochen:\n{err}\n\nDetails: {log}",
                                                  err=msg[7:], log=ERROR_LOG))
        elif msg == "angehalten":
            self.lbl_status.setText(T("Angehalten – „Analyse fortsetzen“ macht an derselben Stelle weiter"))

    def _reset_matching(self):
        if self.worker is not None:
            return
        if QMessageBox.question(self, APP_NAME, T("Alle offenen Paare verwerfen und Abgleich sowie Feinvergleich "
                                                  "beim nächsten Lauf neu berechnen?\nDer Index (Phase 1) bleibt.")) \
                == QMessageBox.Yes:
            self.db.reset_matching()
            self.reload()

    # ------------------------------------------------------------ Auswahl / Vergleich
    def _on_row(self, cur, _prev):
        if not cur.isValid():
            self.current = None
            self.player.stop()
            self.view.clear(T("Paar in der Liste wählen"))
            self._enable_detail(False)
            return
        p = cur.data(PAIR_ROLE)
        if self.current is not None and self.current["id"] == p["id"]:
            return
        self.current = p
        a, b = self.model.files[p["a_id"]], self.model.files[p["b_id"]]
        self._enable_detail(True)
        self._show_info(p, (a, b))
        import numpy as np
        prev_a = np.frombuffer(self.db.preview(a["id"]) or b"", np.uint8) / 255.0
        prev_b = np.frombuffer(self.db.preview(b["id"]) or b"", np.uint8) / 255.0
        self.view.set_pair(prev_a, prev_b, a["duration"], b["duration"],
                           [a["filename"], b["filename"]], p["time_scale"], p["offset_s"])
        res = self._load_compare(p)
        self._cur_cmp = res is not None
        self.view.set_compare(res)
        self.player.set_compare(res)
        self.player.load(p["id"], a["path"], b["path"], p["time_scale"], p["offset_s"])
        self.lbl_pos.setText(T("lädt …"))

    @staticmethod
    def _folder_count(folder: str) -> int | None:
        """Audiodateien im Ordner (frisch gezaehlt -- nach dem Papierkorb stimmt die Zahl sofort)."""
        try:
            with os.scandir(folder) as it:
                return sum(1 for e in it if e.is_file() and os.path.splitext(e.name)[1].lower() in AUDIO_EXTENSIONS)
        except OSError:
            return None

    def _audio_info(self, f: dict) -> dict:
        """Bitrate, Packrate (nur verlustfreie Formate: Groesse zu PCM) und Kanaele -- zwischengespeichert."""
        key = (f["path"], f.get("size"))
        if key not in self._pack_cache:
            out = {}
            size, dur = f.get("size") or 0, f.get("duration") or 0
            if size and dur:
                out["bitrate"] = f"{size * 8 / dur / 1000:.0f} kbit/s"
            try:
                info = probe(f["path"])
                m = re.search(r"PCM_(\d+)", info.codec or "")
                if m and size and dur:
                    out["pack"] = f"{100 * size / (info.sr * info.channels * int(m.group(1)) / 8 * dur):.0f} %"
                if info.channels:
                    out["channels"] = {1: "Mono", 2: "Stereo"}.get(info.channels, T("{n} Kanäle", n=info.channels))
            except Exception:
                pass
            self._pack_cache[key] = out
        return self._pack_cache[key]

    def _cover(self, path: str) -> bytes | None:
        """Coverbild (eingebettet oder Ordnerbild), die letzten Dateien zwischengespeichert."""
        if path not in self._covers:
            if len(self._covers) > 64:
                self._covers.pop(next(iter(self._covers)))
            try:
                self._covers[path] = cover_bytes(path)
            except Exception:
                self._covers[path] = None
        return self._covers[path]

    def _show_info(self, p: dict | None, files: tuple | None):
        """Dateiinfo A/B in Spalten: Datei (mit Anzahl im Ordner), Länge, Abtastrate, Bitrate, Packrate,
        Kanäle, gleich klingender Anteil."""
        for r, btn in enumerate((self.btn_trash_a, self.btn_trash_b)):
            btn.set_cover(None if files is None else self._cover(files[r]["path"]))
        for r in range(2):
            cells = self.info_cells[r]
            if files is None:
                for c in range(1, INFO_COLS):
                    cells[c].setText("")
                continue
            f = files[r]
            folder = f.get("folder") or os.path.dirname(f["path"])
            n = self._folder_count(folder)
            count = "" if n is None else ("<span style='color:#999'>" + (T("1 Datei im Ordner") if n == 1 else
                                                                         T("{n} Dateien im Ordner", n=n)) + "</span>")
            ai = self._audio_info(f)
            green = p["a_green" if r == 0 else "b_green"]
            cells[1].setToolTip(f["path"])
            cells[2].setToolTip(f["path"])
            vals = [os.path.join(folder, ""), f"<b>{f.get('filename') or os.path.basename(f['path'])}</b>", count,
                    fmt_time(f["duration"]), f"{f['sr'] / 1000:g} kHz" if f.get("sr") else "",
                    ai.get("bitrate", ""), T("Packrate {p}", p=ai["pack"]) if "pack" in ai else "", ai.get("channels", ""),
                    ("<span style='color:#6c6'>" + T("{p} % gleich klingend", p=f"{100 * green:.0f}") + "</span>")
                    if green is not None else ""]
            for c, v in enumerate(vals, start=1):
                cells[c].setText(v)

    def _load_compare(self, p: dict):
        blob = self.db.get_diff(p["id"])
        res = compare.unpack(blob) if blob else None
        if res is None and p["id"] not in self._computing:
            self._computing.add(p["id"])
            a, b = self.model.files[p["a_id"]], self.model.files[p["b_id"]]
            off = None if p["source"] == "title" and p["offset_s"] is None else (p["offset_s"] or 0.0)

            def work(pid=p["id"], pa=a["path"], pb=b["path"], ts=p["time_scale"], sh=p["shift_bins"],
                     variant=p["variant"]):
                try:
                    r = compare.compare_pair(pa, pb, ts or 1.0, off, sh or 0.0)
                    self.db.store_compare(pid, classify_compare(r, variant or ""), r, compare.pack(r),
                                          compare.COMPARE_VERSION)
                except Exception as exc:
                    log_error("Feinvergleich", exc)
                    r = None
                self.bridge.compared.emit(pid, r)

            threading.Thread(target=work, daemon=True).start()
        return res

    def _on_compared(self, pid: int, res):
        self._computing.discard(pid)
        if res is None:
            return
        kind = classify_compare(res, (self.current or {}).get("variant") or "")
        self.model.update_pair(pid, similarity=res["similarity"], a_green=res["a_green"],
                               b_green=res["b_green"], sim_median=res["median"], kind=kind,
                               cmp_version=compare.COMPARE_VERSION, offset_s=res["offset_used"])
        if self.current is not None and self.current["id"] == pid:
            self._cur_cmp = True
            self.view.set_compare(res)
            self.player.set_compare(res)
            self.view.off = res["offset_used"]

    def _on_loaded(self, pid: int):
        if self.current is None or self.current["id"] != pid:
            return
        self.view.env = list(self.player.env)
        self.view.dur = list(self.player.dur)
        self.view.update()
        self._on_position(self.player.side, self.player.position())

    def _enable_detail(self, on: bool):
        for w in (self.btn_play, self.btn_a, self.btn_b, self.btn_trash_a, self.btn_trash_b, self.btn_dir_a,
                  self.btn_dir_b, self.btn_notdup, self.btn_reopen):
            w.setEnabled(on)
        reviewed = on and self.current is not None and self.current.get("review") != "open"
        self.btn_reopen.setVisible(reviewed)
        self.btn_notdup.setVisible(not reviewed)
        if not on:
            self._show_info(None, None)
            self.lbl_pos.setText("")

    # ------------------------------------------------------------ Wiedergabe
    def _seek(self, side: int, t: float):
        self.player.seek(side, t)

    def _choose_side(self, side: int):
        if self.player.pair_id is None:
            return
        if side != self.player.side:
            self.player.switch(side)
        self._on_side(self.player.side)

    def _nudge(self, dt: float):
        if self.player.pair_id is not None:
            self.player.seek(self.player.side, max(0.0, self.player.position() + dt))

    def _on_side(self, side: int):
        self.btn_a.setChecked(side == 0)
        self.btn_b.setChecked(side == 1)

    def _on_position(self, side: int, t: float):
        if self.player.pair_id is None:
            return
        partner = self.player.map_time(t, side)
        self.view.set_play(side, t, partner)
        other = "AB"[1 - side]
        self.lbl_pos.setText(f"<b>{'AB'[side]}</b> {fmt_time(t)} / {fmt_time(self.player.dur[side])}   "
                             f"<span style='color:#999'>({other} {fmt_time(partner)})</span>")
        self._on_side(side)

    def _on_play_state(self, playing: bool):
        self.btn_play.setText("⏸  " + T("Pause") if playing else "▶  " + T("Abspielen"))

    # ------------------------------------------------------------ Aktionen
    def _select_after_change(self, row: int, keep_pair: int | None = None):
        self.current = None
        self._apply_filter(-1 if keep_pair is None else keep_pair)
        if keep_pair is None and self.proxy.rowCount():
            self.table.selectRow(min(max(row, 0), self.proxy.rowCount() - 1))

    def _set_review(self, review: str):
        if self.current is None:
            return
        pid = self.current["id"]
        old = self.db.get_review([pid]).get(pid, "open")
        if old == review:
            return
        row = self.table.currentIndex().row()
        self.db.set_review([pid], review)
        self.model.update_pair(pid, review=review)
        label = {"not_dup": T("„Kein Duplikat“"), "open": T("„Wieder in die Liste“")}.get(review, review)

        def undo():
            self.db.set_review([pid], old)
            self.model.update_pair(pid, review=old)
            self._select_after_change(0, pid)
        self._push_undo(label, undo)
        self._select_after_change(row)

    def _trash(self, side: int):
        if self.current is None:
            return
        fid = self.current["a_id" if side == 0 else "b_id"]
        pid = self.current["id"]
        f = self.model.files[fid]
        if QMessageBox.question(self, APP_NAME, T("{side} in den Papierkorb legen?\n\n{path}", side="AB"[side],
                                                  path=f["path"])) \
                != QMessageBox.Yes:
            return
        self.player.stop()
        self.player.wait_idle()
        snap = self.db.snapshot_file(fid)
        try:
            send2trash(f["path"])
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, T("Konnte nicht gelöscht werden:\n{err}", err=exc))
            return
        row = self.table.currentIndex().row()
        self.db.file_removed(fid)
        self.model.remove_file(fid)
        path = f["path"]

        def undo():
            if not recycle.restore(path):
                raise RuntimeError(T("Nicht mehr im Papierkorb:\n{path}", path=path))
            self.db.restore_snapshot(snap)
            self.current = None
            self.reload(keep_pair=pid)
        self._push_undo(T("Papierkorb: {name}", name=os.path.basename(path)), undo)
        self._select_after_change(row)

    # ------------------------------------------------------------ Rueckgaengig
    def _push_undo(self, label: str, fn):
        self._undo.append((label, fn))
        del self._undo[:-UNDO_LIMIT]
        self._update_undo_action()

    def _update_undo_action(self):
        if self._undo:
            self.act_undo.setText(T("Rückgängig: {what}", what=self._undo[-1][0]))
            self.act_undo.setEnabled(True)
        else:
            self.act_undo.setText(T("Rückgängig"))
            self.act_undo.setEnabled(False)

    def _do_undo(self):
        if not self._undo or self._reeval_busy:
            return
        label, fn = self._undo.pop()
        self.player.stop()
        self.player.wait_idle()
        try:
            fn()
            self.lbl_status.setText(T("Rückgängig gemacht: {what}", what=label))
        except Exception as exc:
            log_error("Rückgängig", exc)
            QMessageBox.warning(self, APP_NAME, T("Konnte nicht rückgängig gemacht werden:\n{err}", err=exc))
        self._update_undo_action()

    # ------------------------------------------------------------ Bearbeiten
    def _red_ranges(self, side: int) -> list[tuple[float, float]]:
        """Abweichende Stellen (rot) dieser Seite aus dem Feinvergleich -- Orientierung im Editor."""
        res = self.view.res
        if not res:
            return []
        sim = res["a_sim" if side == 0 else "b_sim"]
        step, win = res["step"], res["win"]
        out = []
        for i, s in enumerate(sim):
            if s == s and s < compare.RED:
                a, b = i * step, i * step + win
                if out and a <= out[-1][1]:
                    out[-1] = (out[-1][0], b)
                else:
                    out.append((a, b))
        return out

    def _open_editor(self, side: int, t: float = 0.0):
        """Rechtsklick in eine Welle: Editor wie in FlacCheck (Schnitt, Kuerzung, Teile, Tags, Name)."""
        if self.current is None or self._reeval_busy:
            return
        from .editor import EditorWindow
        f = self.model.files[self.current["a_id" if side == 0 else "b_id"]]
        partner = self.current["b_id" if side == 0 else "a_id"]
        if not os.path.exists(f["path"]):
            QMessageBox.warning(self, APP_NAME, T("Datei nicht gefunden:\n{path}", path=f["path"]))
            return
        self.player.stop()
        self.player.wait_idle()
        markers = [(a, b, theme.SIM_RED, False) for a, b in self._red_ranges(side)]
        ctx = {"fid": f["id"], "partner": partner, "pair": self.current["id"],
               "snap": self.db.snapshot_file(f["id"]), "tags": editing.read_tags(f["path"])}
        ed = EditorWindow(f["path"], f["duration"], markers, t, self)
        ed.saved.connect(lambda paths, orig, renamed, kept, c=ctx: self._on_edited(c, paths, orig, renamed, kept))
        ed.retagged.connect(lambda old, new, c=ctx: self._on_retagged(c, old, new))
        ed.setAttribute(Qt.WA_DeleteOnClose)
        ed.show()

    def _ensure_root(self, path: str):
        """Liegt eine neue Datei ausserhalb aller Ordner, wird ihr Ordner mit aufgenommen."""
        folder = os.path.normcase(os.path.dirname(path))
        if not any(folder == os.path.normcase(r) or folder.startswith(os.path.join(os.path.normcase(r), ""))
                   for r in self.db.roots()):
            self.db.add_root(os.path.dirname(path))

    def _on_edited(self, ctx: dict, paths: list, original: str, renamed: str, kept: bool):
        """Geschnitten/gekuerzt/geteilt: Die neuen Teile werden sofort neu bewertet (Fingerabdruck +
        Feinvergleich mit dem Partner des Paares). Bis dahin bleibt das Paar in der Liste stehen;
        danach ersetzen die neuen Paare das alte und das beste wird ausgewaehlt."""
        self.player.stop()
        new = []
        for p in paths:
            p = os.path.normpath(p)
            try:
                st = os.stat(p)
            except OSError as exc:
                log_error("Teilstück eintragen", exc)
                continue
            new.append((p, st.st_size, st.st_mtime))
            self._ensure_root(p)
        ctx.update(original=os.path.normpath(original), renamed=os.path.normpath(renamed), kept=kept,
                   new=[p for p, _s, _m in new], reg=new)
        self._reeval_busy = True
        self._enable_detail(False)
        self.view.clear(T("Neubewertung der bearbeiteten Datei läuft …"))
        self.lbl_status.setText(T("Neubewertung: {n} bearbeitete Datei(en) werden analysiert …", n=len(new)))
        b = self.bridge

        def work():
            results = []
            try:
                # Original austragen (ein gekuerzter Teil kann seinen Pfad uebernommen haben), Teile eintragen
                self.db.file_removed(ctx["fid"], ctx["renamed"] if kept else None)
                self.db.register(ctx["reg"])
                results = reevaluate(self.db, ctx["new"], ctx["partner"])
            except Exception as exc:
                log_error("Neubewertung", exc)
            b.reevaluated.emit(ctx, results)
        threading.Thread(target=work, daemon=True).start()

    def _on_reevaluated(self, ctx: dict, results: list):
        self._reeval_busy = False
        new_ids = [self.db.file_id(p) for p in ctx["new"]]
        best = max(results, key=lambda r: r[1])[0] if results else None
        self.current = None
        self.reload(keep_pair=best)
        if best is None and self.proxy.rowCount():
            self.table.selectRow(0)

        def undo():
            for p, fid in zip(ctx["new"], new_ids):
                if fid is not None:
                    self.db.file_removed(fid)
                if os.path.exists(p):
                    send2trash(p)
            if ctx["kept"]:
                move_file(ctx["renamed"], ctx["original"])
            elif not recycle.restore(ctx["renamed"], ctx["original"]):
                raise RuntimeError(T("Original nicht mehr im Papierkorb:\n{path}", path=ctx["renamed"]))
            self.db.restore_snapshot(ctx["snap"])
            self.current = None
            self.reload(keep_pair=ctx["pair"])
        self._push_undo(T("Bearbeiten: {name}", name=os.path.basename(ctx["original"])), undo)
        msg = T("{n} bearbeitete Datei(en) neu bewertet", n=len(ctx["new"]))
        if results:
            msg += " – " + T("beste Übereinstimmung mit dem Partner {p} %", p=f"{max(r[1] for r in results):.0f}")
        self.lbl_status.setText(msg + " · " + T("Abgleich mit dem übrigen Archiv beim nächsten „Analyse fortsetzen“"))

    def _on_retagged(self, ctx: dict, old: str, new: str):
        """Nur Tags/Name geaendert: Klang und Fingerabdruck bleiben gueltig, das Paar bleibt stehen."""
        new = os.path.normpath(new)
        old = os.path.normpath(old)
        try:
            st = os.stat(new)
        except OSError as exc:
            log_error("Umbenennen eintragen", exc)
            return
        fid = ctx["fid"]
        self.db.update_file_meta(fid, new, st.st_size, st.st_mtime, read_tags(new))
        if os.path.normcase(os.path.dirname(new)) != os.path.normcase(os.path.dirname(old)):
            self._ensure_root(new)
        self.player.stop()
        self.current = None
        self.reload(keep_pair=ctx["pair"])

        def undo():
            editing.write_tags(new, {k: ctx["tags"].get(k, "") for k, _ in editing.TAG_FIELDS})
            if os.path.normcase(new) != os.path.normcase(old):
                move_file(new, old)
            st2 = os.stat(old)
            self.db.restore_snapshot(ctx["snap"])
            self.db.update_file_meta(fid, old, st2.st_size, st2.st_mtime, read_tags(old))
            self.current = None
            self.reload(keep_pair=ctx["pair"])
        self._push_undo(T("Tags/Name: {name}", name=os.path.basename(new)), undo)
        self.lbl_status.setText(T("Gespeichert: {name}", name=os.path.basename(new)))

    def _open_dir(self, side: int):
        if self.current is None:
            return
        f = self.model.files[self.current["a_id" if side == 0 else "b_id"]]
        if os.path.exists(f["path"]):
            subprocess.Popen(["explorer", "/select,", f["path"]])
        else:
            os.startfile(f["folder"]) if os.path.isdir(f["folder"] or "") else None

    # ------------------------------------------------------------ Datenbank
    def _switch_db(self, path: str):
        if self.worker is not None:
            QMessageBox.information(self, APP_NAME, T("Bitte erst die laufende Analyse anhalten."))
            return
        self.player.stop()
        self.db.close()
        self.db = Database(path)
        _write_config({**_read_config(), "db": path})
        self.current = None
        self.chk_var.setChecked(self.db.get_setting("variants", "1") == "1")
        self.chk_titles.setChecked(self.db.get_setting("titles", "1") == "1")
        self.kind_combo.set_checked(set(KIND_ORDER) - self._kinds_off())
        self.reload()

    def _open_db(self):
        p, _ = QFileDialog.getOpenFileName(self, T("Datenbank öffnen"), str(APP_DATA_DIR),
                                           T("FlacDup-Datenbank (*.db);;Alle Dateien (*)"))
        if p:
            self._switch_db(p)

    def _new_db(self):
        p, _ = QFileDialog.getSaveFileName(self, T("Neue Datenbank"), str(APP_DATA_DIR / "flacdup_neu.db"),
                                           T("FlacDup-Datenbank (*.db)"))
        if p:
            if os.path.exists(p):
                QMessageBox.warning(self, APP_NAME, T("Die Datei gibt es schon – bitte einen neuen Namen wählen."))
                return
            self._switch_db(p)

    # ------------------------------------------------------------ Sprache / Updates
    def _choose_language(self, code: str):
        set_language(code)
        if code != LANG:
            QMessageBox.information(self, APP_NAME, {
                "de": "Die Sprache wechselt beim nächsten Start von FlacDup.",
                "en": "The language changes the next time FlacDup starts.",
                "fr": "La langue changera au prochain démarrage de FlacDup."}[code])

    def _toggle_updates(self, on: bool):
        self.updates.set_enabled(on)
        if on:
            self.updates.start(force=True)
        else:
            self.btn_update.setVisible(False)

    def _on_update_found(self, version: str):
        self._new_version = version
        self.btn_update.setText(T("Update {v} verfügbar", v=version))
        self.btn_update.setToolTip(T("Version {v} steht auf GitHub bereit – klicken für Download", v=version))
        self.btn_update.setVisible(True)

    def _update_menu(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import QMenu
        from .update_check import RELEASES_URL
        m = QMenu(self)
        a = m.addAction(T("Download-Seite öffnen"))
        a.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(RELEASES_URL)))
        a = m.addAction(T("Diese Version ausblenden"))
        a.triggered.connect(lambda: (self.updates.skip(self._new_version), self.btn_update.setVisible(False)))
        a = m.addAction(T("Nicht mehr nach Updates suchen"))
        a.triggered.connect(lambda: self.act_updates.setChecked(False))
        m.exec(self.btn_update.mapToGlobal(self.btn_update.rect().bottomLeft()))

    def _about(self):
        from .about_dialog import AboutDialog
        AboutDialog(self).exec()

    def closeEvent(self, ev):
        if self.job is not None:
            self.job.stop.set()
        self.player.stop()
        if self.worker is not None:
            self.worker.join(timeout=10)
        ev.accept()
