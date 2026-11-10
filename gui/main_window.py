from __future__ import annotations

import datetime as _dt
import json
import os
import re
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QSplitter, QTableView, QVBoxLayout, QWidget)
from send2trash import send2trash

from engine import compare
from engine.db import Database
from engine.decode import AUDIO_EXTENSIONS, probe
from engine.pipeline import Comparer, Indexer, Matcher, classify_compare, default_workers
from version import APP_NAME, VERSION

from . import theme
from .ab_player import ABPlayer
from .check_combo import CheckCombo
from .compare_view import CompareView, fmt_time
from .pair_model import COL_BAND, PAIR_ROLE, PairFilter, PairModel

APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME
ERROR_LOG = APP_DATA_DIR / "fehler.log"
DEFAULT_DB_PATH = APP_DATA_DIR / "flacdup.db"
CONFIG_PATH = APP_DATA_DIR / "config.json"

KIND_ORDER = ["identisch", "ausschnitt", "variante", "teilweise", "version", "schwach", ""]
DEFAULT_KINDS_OFF = {"schwach"}
REVIEW_CHOICES = [("Offen", "open"), ("Kein Duplikat", "not_dup"), ("Erledigt", "done"), ("Alle", "all")]


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
        self.worker: threading.Thread | None = None
        self.job = None
        self.current: dict | None = None
        self._computing: set[int] = set()
        self._pack_cache: dict = {}        # (Pfad, Groesse) -> Packrate-Text
        self._cur_cmp = False              # Rot/Gruen des aktuellen Paares liegt vor
        # Funde aus Phase 2/3 laufend in die Liste holen (jedes Paar ist sofort gespeichert)
        self.live_timer = QTimer(self)
        self.live_timer.setInterval(15000)
        self.live_timer.timeout.connect(self._live_reload)

        self.player = ABPlayer(self)
        self.player.loaded.connect(self._on_loaded)
        self.player.load_failed.connect(lambda pid, msg: self.view.clear(f"Datei nicht lesbar: {msg}"))
        self.player.position_changed.connect(self._on_position)
        self.player.state_changed.connect(self._on_play_state)
        self.player.side_changed.connect(self._on_side)
        self.player.error.connect(lambda m: QMessageBox.warning(self, APP_NAME, m))

        self._build_ui()
        self._build_menu()
        self._shortcuts()
        self.setAcceptDrops(True)
        self.reload()

    # ------------------------------------------------------------ Aufbau
    def _build_ui(self):
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(8, 8, 8, 8)

        top = QHBoxLayout()
        self.btn_add = QPushButton("Ordner hinzufügen …")
        self.btn_add.setToolTip("Ordner wählen und analysieren (auch per Drag & Drop ins Fenster)")
        self.btn_add.clicked.connect(self._add_folder)
        self.btn_run = QPushButton("▶  Analyse fortsetzen")
        self.btn_run.setToolTip("Alle bekannten Ordner aktualisieren und die drei Phasen abarbeiten:\n"
                                "1 Index (Fingerabdruck je Datei) · 2 Abgleich · 3 Feinvergleich.\n"
                                "Nach einem Abbruch oder Absturz geht es dort weiter, wo es aufgehört hat.")
        self.btn_run.clicked.connect(lambda: self._start_job(self.db.roots()))
        self.btn_stop = QPushButton("■  Anhalten")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_job)
        self.chk_var = QCheckBox("Tempo/Tonhöhe")
        self.chk_var.setToolTip("Auch schneller/langsamer abgespielte und in der Tonhöhe verschobene Fassungen "
                                "suchen (±10 %).\nDer Abgleich dauert damit etwa 20-mal so lange.")
        self.chk_var.setChecked(self.db.get_setting("variants", "1") == "1")
        self.chk_var.toggled.connect(lambda on: self.db.set_setting("variants", "1" if on else "0"))
        self.chk_titles = QCheckBox("Gleiche Titel")
        self.chk_titles.setToolTip("Dateien gleicher Band mit gleichem Titel zusätzlich direkt vergleichen.\n"
                                   "Findet andere Aufnahmen (live, neu eingespielt) und alte Aufnahmen,\n"
                                   "deren Klangschlüssel nicht greifen.")
        self.chk_titles.setChecked(self.db.get_setting("titles", "1") == "1")
        self.chk_titles.toggled.connect(lambda on: self.db.set_setting("titles", "1" if on else "0"))
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(260)
        self.progress.setTextVisible(True)
        self.lbl_status = QLabel("")
        for w in (self.btn_add, self.btn_run, self.btn_stop, self.chk_var, self.chk_titles):
            top.addWidget(w)
        top.addSpacing(12)
        top.addWidget(self.progress)
        top.addWidget(self.lbl_status, 1)
        lay.addLayout(top)

        filt = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Suchen (Band, Titel, Album, Pfad) …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda: self._filter_timer.start())
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self._apply_filter)
        self.kind_combo = CheckCombo("Alle Arten", "Arten")
        self.kind_combo.set_items([(theme.KIND_LABELS[k], k) for k in KIND_ORDER],
                                  set(KIND_ORDER) - self._kinds_off())
        self.kind_combo.selection_changed.connect(self._on_kinds_changed)
        self.review_combo = QComboBox()
        for label, val in REVIEW_CHOICES:
            self.review_combo.addItem(label, val)
        self.review_combo.currentIndexChanged.connect(self._apply_filter)
        self.lbl_count = QLabel("")
        filt.addWidget(self.search, 2)
        filt.addWidget(QLabel("Art:"))
        filt.addWidget(self.kind_combo)
        filt.addWidget(QLabel("Status:"))
        filt.addWidget(self.review_combo)
        filt.addWidget(self.lbl_count, 1)
        lay.addLayout(filt)

        self.model = PairModel(self)
        self.proxy = PairFilter(self)
        self.proxy.setSourceModel(self.model)
        self.table = QTableView()
        self.table.setModel(self.proxy)
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
        self.lbl_a = QLabel("")
        self.lbl_b = QLabel("")
        for lbl in (self.lbl_a, self.lbl_b):
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.view = CompareView()
        self.view.seek_requested.connect(self._seek)
        self.view.edit_requested.connect(self._open_editor)
        dl.addWidget(self.lbl_a)
        dl.addWidget(self.lbl_b)
        dl.addWidget(self.view, 1)

        tr = QHBoxLayout()
        self.btn_play = QPushButton("▶  Abspielen")
        self.btn_play.setToolTip("Leertaste")
        self.btn_play.clicked.connect(self.player.toggle)
        self.btn_a = QPushButton("A")
        self.btn_b = QPushButton("B")
        for k, btn in enumerate((self.btn_a, self.btn_b)):
            btn.setCheckable(True)
            btn.setMinimumWidth(56)
            btn.setStyleSheet("QPushButton:checked { background: #3c6ea0; font-weight: bold; }")
            btn.clicked.connect(lambda _=False, s=k: self._choose_side(s))
        self.btn_a.setToolTip("Taste A – an der entsprechenden Stelle auf A umschalten")
        self.btn_b.setToolTip("Taste B – an der entsprechenden Stelle auf B umschalten")
        self.lbl_pos = QLabel("")
        self.lbl_pos.setMinimumWidth(260)
        hint = QLabel("Leertaste = Abspielen/Pause · Tab = A/B umschalten · Klick in eine Welle = dorthin springen · "
                      "Mausrad = Zoom · Doppelklick = alles · Rechtsklick = bearbeiten")
        hint.setStyleSheet("color: #8a8a8a;")
        tr.addWidget(self.btn_play)
        tr.addWidget(self.btn_a)
        tr.addWidget(self.btn_b)
        tr.addWidget(self.lbl_pos)
        tr.addWidget(hint, 1)
        dl.addLayout(tr)

        act = QHBoxLayout()
        self.btn_trash_a = QPushButton("A in den Papierkorb")
        self.btn_trash_b = QPushButton("B in den Papierkorb")
        self.btn_trash_a.clicked.connect(lambda: self._trash(0))
        self.btn_trash_b.clicked.connect(lambda: self._trash(1))
        self.btn_dir_a = QPushButton("Ordner von A")
        self.btn_dir_b = QPushButton("Ordner von B")
        self.btn_dir_a.clicked.connect(lambda: self._open_dir(0))
        self.btn_dir_b.clicked.connect(lambda: self._open_dir(1))
        self.btn_notdup = QPushButton("Kein Duplikat")
        self.btn_notdup.setToolTip("Paar dauerhaft als „kein Duplikat“ markieren (Taste N)")
        self.btn_notdup.clicked.connect(lambda: self._set_review("not_dup"))
        self.btn_done = QPushButton("Erledigt")
        self.btn_done.setToolTip("Paar als bearbeitet markieren – beide Dateien bleiben (Taste E)")
        self.btn_done.clicked.connect(lambda: self._set_review("done"))
        self.btn_reopen = QPushButton("Wieder öffnen")
        self.btn_reopen.clicked.connect(lambda: self._set_review("open"))
        for w in (self.btn_trash_a, self.btn_dir_a, self.btn_trash_b, self.btn_dir_b):
            act.addWidget(w)
        act.addStretch(1)
        for w in (self.btn_reopen, self.btn_notdup, self.btn_done):
            act.addWidget(w)
        dl.addLayout(act)

        split = QSplitter(Qt.Vertical)
        split.addWidget(self.table)
        split.addWidget(detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)
        self.setCentralWidget(central)
        self._enable_detail(False)

    def _build_menu(self):
        m = self.menuBar().addMenu("&Datei")
        a = QAction("Ordner hinzufügen …", self)
        a.triggered.connect(self._add_folder)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Datenbank öffnen …", self)
        a.triggered.connect(self._open_db)
        m.addAction(a)
        a = QAction("Neue Datenbank …", self)
        a.triggered.connect(self._new_db)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Nicht lesbare Dateien …", self)
        a.triggered.connect(self._show_unreadable)
        m.addAction(a)
        a = QAction("Abgleich und Feinvergleich neu berechnen", self)
        a.setToolTip("Index bleibt erhalten; offene Paare werden neu gesucht (Prüfstand bleibt).")
        a.triggered.connect(self._reset_matching)
        m.addAction(a)
        m.addSeparator()
        a = QAction("Beenden", self)
        a.triggered.connect(self.close)
        m.addAction(a)
        h = self.menuBar().addMenu("&Hilfe")
        a = QAction(f"Über {APP_NAME} …", self)
        a.triggered.connect(self._about)
        h.addAction(a)

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
        sc(Qt.Key_E, lambda: self._set_review("done"))
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
                todo.append(f"{c['pending']} Dateien ohne Fingerabdruck")
            if c.get("unmatched"):
                todo.append(f"{c['unmatched']} nicht abgeglichen")
            if c.get("uncompared"):
                todo.append(f"{c['uncompared']} Paare ohne Feinvergleich")
            base = (f"{c.get('done', 0)} Dateien im Index, {c.get('pairs', 0)} Paare"
                    + (f" · {c['error']} nicht lesbar" if c.get("error") else ""))
            self.lbl_status.setText(base + (" · offen: " + ", ".join(todo) if todo else "")
                                    if roots else "Noch keine Ordner – „Ordner hinzufügen …“ "
                                                  "oder Ordner ins Fenster ziehen")
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
            self.lbl_a.setText(self._file_line("A", a, p["a_green"]))
            self.lbl_b.setText(self._file_line("B", b, p["b_green"]))
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
        self.lbl_count.setText(f"{n} von {self.model.rowCount()} Paaren")
        if cur is not None:
            for r in range(n):
                if self.proxy.index(r, 0).data(PAIR_ROLE)["id"] == cur:
                    self.table.selectRow(r)
                    return

    # ------------------------------------------------------------ Phasen
    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Ordner mit Musik wählen",
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
            QMessageBox.information(self, APP_NAME, "Es läuft bereits eine Analyse.")
            return
        targets = list(dict.fromkeys(targets))
        b = self.bridge
        workers = default_workers()
        variants, titles = self.chk_var.isChecked(), self.chk_titles.isChecked()

        def run():
            msg = "fertig"
            try:
                for name, make in (
                        ("Phase 1 von 3: Index", lambda: Indexer(self.db, targets, workers, b.status.emit,
                                                                 b.progress.emit)),
                        ("Phase 2 von 3: Abgleich", lambda: Matcher(self.db, variants, b.status.emit,
                                                                    b.progress.emit)),
                        ("Phase 3 von 3: Feinvergleich", lambda: Comparer(self.db, workers, titles,
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
            self.lbl_status.setText("Wird angehalten …")

    def _on_status(self, text: str):
        self.lbl_status.setText(text)

    def _on_progress(self, done: int, total: int):
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(done)

    def _on_phase(self, name: str):
        if name == "reload":
            self._live_reload()
        else:
            self.progress.setFormat(f"{name.split(':')[0]}: %p %")
            self.progress.setValue(0)
            if name.startswith(("Phase 2", "Phase 3")):
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
            QMessageBox.warning(self, APP_NAME, f"Analyse abgebrochen:\n{msg[7:]}\n\nDetails: {ERROR_LOG}")
        elif msg == "angehalten":
            self.lbl_status.setText("Angehalten – „Analyse fortsetzen“ macht an derselben Stelle weiter")

    def _reset_matching(self):
        if self.worker is not None:
            return
        if QMessageBox.question(self, APP_NAME, "Alle offenen Paare verwerfen und Abgleich sowie Feinvergleich "
                                                "beim nächsten Lauf neu berechnen?\nDer Index (Phase 1) bleibt.") \
                == QMessageBox.Yes:
            self.db.reset_matching()
            self.reload()

    # ------------------------------------------------------------ Auswahl / Vergleich
    def _on_row(self, cur, _prev):
        if not cur.isValid():
            self.current = None
            self.player.stop()
            self.view.clear("Paar in der Liste wählen")
            self._enable_detail(False)
            return
        p = cur.data(PAIR_ROLE)
        if self.current is not None and self.current["id"] == p["id"]:
            return
        self.current = p
        a, b = self.model.files[p["a_id"]], self.model.files[p["b_id"]]
        self._enable_detail(True)
        self.lbl_a.setText(self._file_line("A", a, p["a_green"]))
        self.lbl_b.setText(self._file_line("B", b, p["b_green"]))
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
        self.lbl_pos.setText("lädt …")

    @staticmethod
    def _folder_count(folder: str) -> int | None:
        """Audiodateien im Ordner (frisch gezaehlt -- nach dem Papierkorb stimmt die Zahl sofort)."""
        try:
            with os.scandir(folder) as it:
                return sum(1 for e in it if e.is_file() and os.path.splitext(e.name)[1].lower() in AUDIO_EXTENSIONS)
        except OSError:
            return None

    def _pack_info(self, f: dict) -> str:
        """'Packrate 58 % · 912 kbit/s' -- Packrate nur bei verlustfreien Formaten (Groesse zu PCM)."""
        key = (f["path"], f.get("size"))
        cache = self._pack_cache
        if key not in cache:
            parts = []
            size, dur = f.get("size") or 0, f.get("duration") or 0
            try:
                info = probe(f["path"])
                m = re.search(r"PCM_(\d+)", info.codec or "")
                if m and size and dur:
                    raw = info.sr * info.channels * int(m.group(1)) / 8 * dur
                    parts.append(f"Packrate {100 * size / raw:.0f} %")
                if info.channels:
                    parts.append({1: "Mono", 2: "Stereo"}.get(info.channels, f"{info.channels} Kanäle"))
            except Exception:
                pass
            if size and dur:
                parts.insert(1 if parts and parts[0].startswith("Packrate") else 0,
                             f"{size * 8 / dur / 1000:.0f} kbit/s")
            cache[key] = "  ·  ".join(parts)
        return cache[key]

    def _file_line(self, side: str, f: dict, green) -> str:
        info = f"<b>{side}</b>  {f['path']}"
        n = self._folder_count(f.get("folder") or os.path.dirname(f["path"]))
        if n is not None:
            info += f"  <span style='color:#999'>({n} {'Datei' if n == 1 else 'Dateien'} im Ordner)</span>"
        info += f"  ·  {fmt_time(f['duration'])}"
        if f.get("sr"):
            info += f"  ·  {f['sr'] / 1000:g} kHz"
        pack = self._pack_info(f)
        if pack:
            info += f"  ·  {pack}"
        if green is not None:
            info += f"  ·  <span style='color:#6c6'>{100 * green:.0f} % gleich klingend</span>"
        return info

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
                  self.btn_dir_b, self.btn_notdup, self.btn_done, self.btn_reopen):
            w.setEnabled(on)
        if not on:
            self.lbl_a.setText("")
            self.lbl_b.setText("")
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
        self.btn_play.setText("⏸  Pause" if playing else "▶  Abspielen")

    # ------------------------------------------------------------ Aktionen
    def _set_review(self, review: str):
        if self.current is None:
            return
        pid = self.current["id"]
        row = self.table.currentIndex().row()
        self.db.set_review([pid], review)
        self.model.update_pair(pid, review=review)
        self.current = None
        self._apply_filter(-1)
        if self.proxy.rowCount():
            self.table.selectRow(min(row, self.proxy.rowCount() - 1))

    def _trash(self, side: int):
        if self.current is None:
            return
        fid = self.current["a_id" if side == 0 else "b_id"]
        f = self.model.files[fid]
        if QMessageBox.question(self, APP_NAME, f"{'AB'[side]} in den Papierkorb legen?\n\n{f['path']}") \
                != QMessageBox.Yes:
            return
        self.player.stop()
        try:
            send2trash(f["path"])
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Konnte nicht gelöscht werden:\n{exc}")
            return
        row = self.table.currentIndex().row()
        self.db.file_removed(fid)
        self.model.remove_file(fid)
        self.current = None
        self._apply_filter(-1)
        if self.proxy.rowCount():
            self.table.selectRow(min(row, self.proxy.rowCount() - 1))

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
        if self.current is None:
            return
        from .editor import EditorWindow
        f = self.model.files[self.current["a_id" if side == 0 else "b_id"]]
        if not os.path.exists(f["path"]):
            QMessageBox.warning(self, APP_NAME, f"Datei nicht gefunden:\n{f['path']}")
            return
        self.player.pause()
        markers = [(a, b, theme.SIM_RED, False) for a, b in self._red_ranges(side)]
        ed = EditorWindow(f["path"], f["duration"], markers, t, self)
        ed.saved.connect(lambda paths, orig, kept, fid=f["id"]: self._on_edited(fid, paths, orig, kept))
        ed.retagged.connect(lambda old, new, fid=f["id"]: self._on_retagged(fid, old, new))
        ed.setAttribute(Qt.WA_DeleteOnClose)
        ed.show()

    def _ensure_root(self, path: str):
        """Liegt eine neue Datei ausserhalb aller Ordner, wird ihr Ordner mit aufgenommen."""
        folder = os.path.normcase(os.path.dirname(path))
        if not any(folder == os.path.normcase(r) or folder.startswith(os.path.join(os.path.normcase(r), ""))
                   for r in self.db.roots()):
            self.db.add_root(os.path.dirname(path))

    def _on_edited(self, fid: int, paths: list, original: str, kept_as: str):
        """Geschnittene/gekuerzte Teile als ungepruefte Dateien eintragen; das Original faellt mit
        seinen Paaren heraus (oder bleibt unter neuem Namen, falls behalten)."""
        self.player.stop()
        self.db.file_removed(fid, os.path.normpath(kept_as) if kept_as else None)
        if not kept_as:
            self.model.remove_file(fid)
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
        self.db.register(new)
        self._after_edit(f"{len(new)} bearbeitete Datei(en) eingetragen")

    def _on_retagged(self, fid: int, old: str, new: str):
        """Nur Tags/Name geaendert: Eintrag zieht mit um (Paare und Pruefstand bleiben) und wird
        als ungeprueft neu eingelesen."""
        new = os.path.normpath(new)
        try:
            st = os.stat(new)
        except OSError as exc:
            log_error("Umbenennen eintragen", exc)
            return
        if os.path.normcase(new) != os.path.normcase(os.path.normpath(old)):
            self.db.relocate(fid, new, st.st_size, st.st_mtime)
            self._ensure_root(new)
        self.db.register([(new, st.st_size, st.st_mtime)])
        f = self.model.files.get(fid)
        if f is not None:
            f.update(path=new, folder=os.path.dirname(new), filename=os.path.basename(new))
        self._after_edit("Änderungen gespeichert")

    def _after_edit(self, what: str):
        busy = self.worker is not None
        self.current = None
        self.reload()
        if not busy:
            self._refresh_status()
        self.lbl_status.setText(f"{what} – {'werden nach diesem Lauf' if busy else 'werden beim nächsten'} "
                                "„Analyse fortsetzen“ neu analysiert und abgeglichen")

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
            QMessageBox.information(self, APP_NAME, "Bitte erst die laufende Analyse anhalten.")
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
        p, _ = QFileDialog.getOpenFileName(self, "Datenbank öffnen", str(APP_DATA_DIR),
                                           "FlacDup-Datenbank (*.db);;Alle Dateien (*)")
        if p:
            self._switch_db(p)

    def _new_db(self):
        p, _ = QFileDialog.getSaveFileName(self, "Neue Datenbank", str(APP_DATA_DIR / "flacdup_neu.db"),
                                           "FlacDup-Datenbank (*.db)")
        if p:
            if os.path.exists(p):
                QMessageBox.warning(self, APP_NAME, "Die Datei gibt es schon – bitte einen neuen Namen wählen.")
                return
            self._switch_db(p)

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
