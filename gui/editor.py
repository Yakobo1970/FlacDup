"""Bearbeitungsfenster: Track teilen oder Bereiche herausschneiden, Tags/Dateinamen je Teilstueck
(auch nur Tags oder Dateiname aendern, ohne Schnitt). Uebernommen aus FlacCheck.

Bedienung in der Wellenform:
  Klick = Cursor setzen · Ziehen = Bereich auswaehlen · Mausrad = Zoom · Umschalt+Rad = verschieben
  Leertaste = ab Cursor abspielen/stoppen · S = Schnittpunkt am Cursor · Entf = Auswahl entfernen
  Strg+Z = rueckgaengig
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                           QShortcut)
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QMessageBox, QPushButton, QScrollBar, QTableWidget, QTableWidgetItem,
                               QTreeView, QVBoxLayout, QWidget)

from engine import editing
from engine.decode import AUDIO_EXTENSIONS, DecodeReport, iter_mono_blocks
from paths import ASSETS_DIR

from .player import LoopPlayer
from .compare_view import fmt_time

BIN = 512  # Samples je Wellenform-Bin


class _PeakLoader(QThread):
    done = Signal(object, float)   # peaks (n, 2), Dauer
    failed = Signal(str)

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def run(self):
        try:
            mins, maxs, total, sr = [], [], 0, 44100
            rest = np.zeros(0, np.float32)
            for block, sr in iter_mono_blocks(self.path, DecodeReport()):
                buf = np.concatenate((rest, block)) if rest.size else block
                n = buf.size // BIN * BIN
                fr = buf[:n].reshape(-1, BIN)
                mins.append(fr.min(1))
                maxs.append(fr.max(1))
                rest = buf[n:]
                total += block.size
            peaks = np.stack((np.concatenate(mins), np.concatenate(maxs)), 1) if mins else np.zeros((0, 2))
            self.done.emit(peaks, total / sr)
        except Exception as exc:
            self.failed.emit(str(exc))


class WaveEdit(QWidget):
    changed = Signal()            # Cursor/Auswahl/Ansicht geaendert

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.peaks = np.zeros((0, 2))
        self.duration = 0.0
        self.v0, self.v1 = 0.0, 1.0
        self.cursor = 0.0
        self.sel: tuple[float, float] | None = None
        self.splits: list[float] = []
        self.removed: list[tuple[float, float]] = []
        self.markers: list[tuple[float, float, QColor, bool]] = []
        self.play_pos = -1.0
        self._bg: QPixmap | None = None   # zwischengespeichertes Bild ohne Abspielposition
        self._bg_key = None
        self._drag_from: float | None = None

    # ------------------------------------------------------------ Koordinaten
    def x_of(self, t):
        return (t - self.v0) / max(self.v1 - self.v0, 1e-9) * self.width()

    def t_of(self, x):
        return min(max(self.v0 + x / max(self.width(), 1) * (self.v1 - self.v0), 0.0), self.duration)

    def set_view(self, v0, v1):
        span = max(min(v1 - v0, self.duration), 0.5)
        v0 = min(max(v0, 0.0), max(self.duration - span, 0.0))
        self.v0, self.v1 = v0, v0 + span
        self.update()
        self.changed.emit()

    # ------------------------------------------------------------ Zeichnen
    def paintEvent(self, ev):
        # Alles ausser der Abspielposition wird als Bild zwischengespeichert. Waehrend der
        # Wiedergabe kommt alle 40 ms ein neues Bild; die ganze Wellenform jedes Mal neu zu
        # zeichnen blockierte Python so lange, dass der Audio-Callback zu spaet kam (Stottern).
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, self.v0, self.v1, id(self.peaks), self.peaks.shape,
               tuple(self.removed), tuple((a, b, r) for a, b, _c, r in self.markers), self.sel,
               tuple(self.splits), self.cursor)
        if key != self._bg_key:
            pm = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
            pm.setDevicePixelRatio(dpr)
            bp = QPainter(pm)
            self._paint_static(bp)
            bp.end()
            self._bg, self._bg_key = pm, key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._bg)
        if self.play_pos >= 0:
            p.setPen(QPen(QColor(120, 255, 120), 1.5))
            x = self.x_of(self.play_pos)
            p.drawLine(QPointF(x, 18), QPointF(x, self.height()))
        p.end()

    def _paint_static(self, p: QPainter):
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(20, 22, 25))
        top = 18
        mid = top + (h - top) / 2
        amp = (h - top) / 2 - 2
        # Zeitachse
        p.setPen(QColor(120, 120, 120))
        span = self.v1 - self.v0
        step = next((s for s in (0.1, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if span / s <= 12), 1200)
        t = (self.v0 // step + 1) * step
        while t < self.v1:
            x = self.x_of(t)
            p.drawLine(QPointF(x, top - 4), QPointF(x, top))
            p.drawText(QPointF(x + 3, top - 5), fmt_time(t) if step >= 1 else f"{t:.1f}")
            t += step
        # entfernte Bereiche
        for a, b in self.removed:
            p.fillRect(QRectF(self.x_of(a), top, self.x_of(b) - self.x_of(a), h - top), QColor(90, 30, 30, 160))
        # abweichende Stellen aus dem Vergleich als Orientierung (oben schmal)
        for a, b, col, removed in self.markers:
            c = QColor(col)
            c.setAlpha(60 if removed else 150)
            p.fillRect(QRectF(self.x_of(a), top, max(2, self.x_of(b) - self.x_of(a)), 6), c)
        # Wellenform
        if self.peaks.shape[0] and self.duration > 0:
            n = self.peaks.shape[0]
            b0 = int(self.v0 / self.duration * n)
            b1 = max(b0 + 1, int(np.ceil(self.v1 / self.duration * n)))
            seg = self.peaks[b0:b1]
            cols = max(2, w)
            edges = np.linspace(0, seg.shape[0], cols + 1).astype(int)
            edges = np.minimum(edges[:-1], seg.shape[0] - 1)
            lo = np.minimum.reduceat(seg[:, 0], edges)
            hi = np.maximum.reduceat(seg[:, 1], edges)
            xs = np.arange(cols) * (w / cols)
            topl = [QPointF(float(x), float(mid - v * amp)) for x, v in zip(xs, np.clip(hi, -1, 1))]
            botl = [QPointF(float(x), float(mid - v * amp)) for x, v in zip(xs[::-1], np.clip(lo[::-1], -1, 1))]
            path = QPainterPath()
            path.addPolygon(QPolygonF(topl + botl))
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(110, 190, 160))
            p.drawPath(path)
        else:
            p.setPen(QColor(160, 160, 160))
            p.drawText(self.rect(), Qt.AlignCenter, "Wellenform wird geladen …")
        # entfernte Bereiche deutlich rot schraffiert darueber
        for a, b in self.removed:
            r = QRectF(self.x_of(a), top, max(2.0, self.x_of(b) - self.x_of(a)), h - top)
            p.fillRect(r, QColor(20, 22, 25, 170))
            p.fillRect(r, QBrush(QColor(230, 70, 70, 150), Qt.BDiagPattern))
            p.setPen(QPen(QColor(230, 70, 70), 1.5))
            p.drawLine(QPointF(r.left(), top), QPointF(r.left(), h))
            p.drawLine(QPointF(r.right(), top), QPointF(r.right(), h))
            if r.width() > 70:
                p.fillRect(QRectF(r.center().x() - 45, mid - 10, 90, 20), QColor(20, 22, 25, 220))
                p.setPen(QColor(255, 140, 140))
                p.drawText(QRectF(r.center().x() - 45, mid - 10, 90, 20), Qt.AlignCenter, "wird entfernt")
        # Auswahl
        if self.sel:
            a, b = self.sel
            p.fillRect(QRectF(self.x_of(a), top, self.x_of(b) - self.x_of(a), h - top), QColor(80, 140, 255, 70))
        # Schnittpunkte: kraeftige rote Linie mit Fahne, damit sie der Cursor nicht verdeckt
        for k, s in enumerate(sorted(self.splits), 1):
            x = self.x_of(s)
            p.setPen(QPen(QColor(255, 80, 80), 3))
            p.drawLine(QPointF(x, top), QPointF(x, h))
            p.fillRect(QRectF(x + 2, top + 2, 70, 16), QColor(255, 80, 80))
            p.setPen(QColor(255, 255, 255))
            p.drawText(QRectF(x + 2, top + 2, 70, 16), Qt.AlignCenter, f"Schnitt {k}")
        # Cursor (die Abspielposition zeichnet paintEvent darueber)
        p.setPen(QPen(QColor(255, 255, 255), 1.0, Qt.DashLine))
        x = self.x_of(self.cursor)
        p.drawLine(QPointF(x, top), QPointF(x, h))
        p.end()

    # ------------------------------------------------------------ Maus
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self.duration:
            self._drag_from = self.t_of(e.position().x())
            self.setFocus()

    def mouseMoveEvent(self, e):
        if self._drag_from is not None:
            t = self.t_of(e.position().x())
            if abs(self.x_of(t) - self.x_of(self._drag_from)) > 3:
                self.sel = (min(t, self._drag_from), max(t, self._drag_from))
                self.update()
        elif self.duration:
            self.setToolTip(fmt_time(self.t_of(e.position().x())))

    def mouseReleaseEvent(self, e):
        if self._drag_from is None:
            return
        t = self.t_of(e.position().x())
        if abs(self.x_of(t) - self.x_of(self._drag_from)) <= 3:
            self.cursor = t
            self.sel = None
        else:
            self.cursor = min(t, self._drag_from)
        self._drag_from = None
        self.update()
        self.changed.emit()

    def wheelEvent(self, e):
        if not self.duration:
            return
        dy = e.angleDelta().y()
        span = self.v1 - self.v0
        if e.modifiers() & Qt.ShiftModifier:
            self.set_view(self.v0 - dy / 120 * span * 0.15, self.v1 - dy / 120 * span * 0.15)
            return
        t = self.t_of(e.position().x())
        f = 0.8 if dy > 0 else 1.25
        frac = (t - self.v0) / max(span, 1e-9)
        new = span * f
        self.set_view(t - frac * new, t - frac * new + new)


class EditorWindow(QWidget):
    saved = Signal(list, str, str)      # neue Pfade, Original, neuer Ort des Originals ('' = Papierkorb)
    retagged = Signal(str, str)         # nur Tags/Name geaendert: alter Pfad, neuer Pfad

    COLS = ["Teil", "Bereich", "Dauer", "Dateiname"] + [label for _, label in editing.TAG_FIELDS]

    ORIG_PREFIX = "(Original)"

    def __init__(self, path: str, duration: float, markers, cursor: float = 0.0, parent=None):
        super().__init__(parent, Qt.Window)
        self.src = path
        self._start_cursor = cursor
        self.setWindowTitle(f"Bearbeiten – {os.path.basename(self.src)}")
        icon = ASSETS_DIR / "editor_icon.ico"
        if icon.exists():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1300, 760)
        self.orig_tags = editing.read_tags(self.src)
        self.folder = os.path.dirname(self.src)
        self._undo: list[tuple[list, list]] = []
        self._edits: dict[int, dict[int, str]] = {}   # Teil -> Spalte -> vom Nutzer geaenderter Text
        self._filling = False
        self.player = LoopPlayer(self)
        self.player.position_changed.connect(self._on_play_pos)

        lay = QVBoxLayout(self)
        info = QLabel(f"<b>{os.path.basename(self.src)}</b><br><span style='color:#999'>{self.folder}</span>")
        info.setTextFormat(Qt.RichText)
        lay.addWidget(info)
        hint = QLabel("Klick = Cursor · Ziehen = Auswahl · Mausrad = Zoom · Umschalt+Rad = verschieben · "
                      "Leertaste = ab Cursor abspielen · S = Schnittpunkt · Entf = Auswahl entfernen · Strg+Z = rückgängig"
                      + ("" if not self.src.lower().endswith(".mp3") else
                         "  ·  MP3 wird ohne Neukodierung geschnitten (Schnitte rasten auf ~26 ms ein)"))
        hint.setStyleSheet("color:#999")
        lay.addWidget(hint)

        self.wave = WaveEdit(self)
        self.wave.markers = markers
        lay.addWidget(self.wave, 2)
        self.scroll = QScrollBar(Qt.Horizontal, self)
        self.scroll.valueChanged.connect(self._on_scroll)
        lay.addWidget(self.scroll)

        bar = QHBoxLayout()
        self.lbl_pos = QLabel()
        b_play = QPushButton("▶ ab Cursor")
        b_loop = QPushButton("⟳ Auswahl in Schleife")
        b_stop = QPushButton("■")
        b_split = QPushButton("Schnittpunkt am Cursor (S)")
        b_cut = QPushButton("Auswahl entfernen (Entf)")
        b_undo = QPushButton("Rückgängig")
        b_reset = QPushButton("Alles zurücksetzen")
        b_all = QPushButton("Ganzer Track")
        for b in (b_play, b_loop, b_stop, b_split, b_cut, b_undo, b_reset, b_all):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.lbl_pos)
        lay.addLayout(bar)
        b_play.clicked.connect(self._play_from_cursor)
        b_loop.clicked.connect(self._play_selection)
        b_stop.clicked.connect(self.player.stop)
        b_split.clicked.connect(self._split)
        b_cut.clicked.connect(self._cut)
        b_undo.clicked.connect(self._undo_step)
        b_reset.clicked.connect(self._reset)
        b_all.clicked.connect(lambda: self.wave.set_view(0, self.wave.duration))

        # letzte Spalte: Kontrollkaestchen, ob das Teilstueck gespeichert wird
        self.save_col = len(self.COLS)
        self.table = QTableWidget(0, len(self.COLS) + 1, self)
        self.table.setHorizontalHeaderLabels(self.COLS + ["Speichern"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(3, 360)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.AllEditTriggers)
        self.table.itemChanged.connect(self._on_item_changed)
        lay.addWidget(self.table, 1)

        opts = QHBoxLayout()
        self.lbl_folder = QLabel()
        b_folder = QPushButton("Zielordner …")
        b_folder.clicked.connect(self._choose_folder)
        self.chk_keep = QCheckBox("Original nicht löschen/überschreiben")
        self.chk_keep.setToolTip("Normalerweise wird das Original nach dem Schneiden als »(Original) Name« in den "
                                 "Windows-Papierkorb gelegt.\nMit Häkchen bleibt es unter diesem Namen zusätzlich im "
                                 "Originalordner.\nNur Tags/Name geändert: die Datei selbst wird angepasst.")
        opts.addWidget(self.lbl_folder, 1)
        opts.addWidget(b_folder)
        opts.addWidget(self.chk_keep)
        lay.addLayout(opts)
        bottom = QHBoxLayout()
        self.lbl_summary = QLabel()
        self.b_save = b_save = QPushButton("Speichern")
        b_save.setDefault(True)
        b_save.setMinimumWidth(b_save.fontMetrics().horizontalAdvance("1 gekürzte Datei speichern") + 40)
        b_close = QPushButton("Schließen")
        b_save.clicked.connect(self._save)
        b_close.clicked.connect(self.close)
        bottom.addWidget(self.lbl_summary, 1)
        bottom.addWidget(b_save)
        bottom.addWidget(b_close)
        lay.addLayout(bottom)

        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self._toggle_play)
        QShortcut(QKeySequence(Qt.Key_S), self, activated=self._split)
        QShortcut(QKeySequence(Qt.Key_Delete), self, activated=self._cut)
        QShortcut(QKeySequence.Undo, self, activated=self._undo_step)
        self.wave.changed.connect(self._on_wave_changed)

        self._update_folder_label()
        self.wave.duration = duration or 0.0
        self._rebuild_parts()
        self._loader = _PeakLoader(self.src)
        self._loader.done.connect(self._on_peaks)
        self._loader.failed.connect(lambda m: QMessageBox.warning(self, "Bearbeiten", f"Nicht lesbar: {m}"))
        self._loader.start()

    # ------------------------------------------------------------ Laden / Ansicht
    def _on_peaks(self, peaks, duration):
        self.wave.peaks = peaks
        self.wave.duration = duration
        self.wave.set_view(0, duration)
        if 0 < self._start_cursor < duration:
            self.wave.cursor = self._start_cursor
        self._rebuild_parts()
        self._on_wave_changed()

    def _on_scroll(self, v):
        span = self.wave.v1 - self.wave.v0
        t0 = v / 1000.0
        if abs(t0 - self.wave.v0) > 1e-3:
            self.wave.set_view(t0, t0 + span)

    def _on_wave_changed(self):
        w = self.wave
        span = w.v1 - w.v0
        self.scroll.blockSignals(True)
        self.scroll.setRange(0, int(max(w.duration - span, 0) * 1000))
        self.scroll.setPageStep(int(span * 1000))
        self.scroll.setValue(int(w.v0 * 1000))
        self.scroll.blockSignals(False)
        sel = f"  ·  Auswahl {fmt_time(w.sel[0])} – {fmt_time(w.sel[1])} ({w.sel[1] - w.sel[0]:.1f} s)" if w.sel else ""
        self.lbl_pos.setText(f"Cursor {fmt_time(w.cursor)}{sel}")

    def _on_play_pos(self, _fid, pos):
        self.wave.play_pos = pos
        self.wave.update()

    # ------------------------------------------------------------ Wiedergabe
    def _toggle_play(self):
        if self.player.playing:
            self.player.stop()
        else:
            self._play_from_cursor()

    def _play_from_cursor(self):
        """Ab Cursor so abspielen, wie das Ergebnis klingt: entfernte Bereiche werden uebersprungen."""
        self.player.play(-1, self.src, self.wave.cursor, self.wave.duration, loop=False,
                         ranges=self._kept_ranges(self.wave.cursor))

    def _kept_ranges(self, start: float) -> list[tuple[float, float]]:
        out, t = [], start
        for a, b in sorted(self.wave.removed):
            if b <= t:
                continue
            if a > t:
                out.append((t, a))
            t = max(t, b)
        if t < self.wave.duration:
            out.append((t, self.wave.duration))
        return out

    def _play_selection(self):
        if self.wave.sel:
            self.player.play(-1, self.src, self.wave.sel[0], self.wave.sel[1], loop=True)

    # ------------------------------------------------------------ Bearbeiten
    def _push_undo(self):
        self._undo.append((list(self.wave.splits), list(self.wave.removed)))

    def _split(self):
        t = self.wave.cursor
        if 0.05 < t < self.wave.duration - 0.05 and all(abs(t - s) > 0.05 for s in self.wave.splits):
            self._push_undo()
            self.wave.splits.append(t)
            self._changed()

    def _cut(self):
        if not self.wave.sel:
            return
        self._push_undo()
        a, b = self.wave.sel
        merged = []
        for x, y in sorted(self.wave.removed + [(a, b)]):
            if merged and x <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(y, merged[-1][1]))
            else:
                merged.append((x, y))
        self.wave.removed = merged
        self.wave.sel = None
        self._changed()

    def _undo_step(self):
        if self._undo:
            self.wave.splits, self.wave.removed = self._undo.pop()
            self._changed()

    def _reset(self):
        if self.wave.splits or self.wave.removed:
            self._push_undo()
            self.wave.splits, self.wave.removed = [], []
            self._changed()

    def _changed(self):
        self.wave.update()
        self._rebuild_parts()
        self._on_wave_changed()

    # ------------------------------------------------------------ Teilstuecke
    def _parts(self):
        return editing.plan_parts(self.wave.duration, self.wave.splits, self.wave.removed)

    def _defaults(self, i: int, n: int) -> list[str]:
        stem, ext = os.path.splitext(os.path.basename(self.src))
        name = os.path.basename(self.src) if n == 1 else f"{stem} (Teil {i}){ext}"
        vals = []
        for key, _ in editing.TAG_FIELDS:
            v = self.orig_tags.get(key, "")
            if key == "title" and n > 1:
                v = f"{v} (Teil {i})" if v else f"Teil {i}"
            vals.append(v)
        return [name] + vals

    def _rebuild_parts(self):
        parts = self._parts()
        self._filling = True
        self.table.setRowCount(len(parts))
        kept = 0
        n_kept = sum(1 for r in parts if r)
        for i, ranges in enumerate(parts, 1):
            # Vorgabenamen nur ueber die gespeicherten Teile durchnummerieren (weggelassene zaehlen nicht)
            dflt = self._defaults(kept + 1, n_kept) if ranges else self._defaults(i, len(parts))
            rng = " + ".join(f"{fmt_time(a)}–{fmt_time(b)}" for a, b in ranges) or "komplett entfernt"
            cells = [str(i), rng, f"{editing.part_duration(ranges):.1f} s" if ranges else "–"]
            cells += [self._edits.get(i, {}).get(c, dflt[c - 3]) for c in range(3, len(self.COLS))]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c < 3 or not ranges:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if not ranges:
                    item.setForeground(QColor(140, 140, 140))
                self.table.setItem(i - 1, c, item)
            chk = QTableWidgetItem("speichern" if ranges else "weglassen")
            chk.setFlags((chk.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable)
            chk.setCheckState(Qt.Checked if ranges else Qt.Unchecked)
            chk.setToolTip("Haken entfernen: dieses Teilstück nicht speichern (wird in der Wellenform rot "
                           "als entfernt markiert)")
            self.table.setItem(i - 1, self.save_col, chk)
            kept += bool(ranges)
        self._filling = False
        removed = sum(b - a for a, b in self.wave.removed)
        self.lbl_summary.setText(f"{kept} Teilstück(e) · {len(self.wave.splits)} Schnittpunkt(e) · "
                                 f"{removed:.1f} s werden entfernt (nicht gespeichert)")
        self.b_save.setText(self._save_label(kept))
        self.b_save.setEnabled((bool(self.wave.splits or self.wave.removed) or self._meta_changed()) and kept > 0)

    def _meta_changed(self) -> bool:
        """Ohne Schnitt: wurden Dateiname, Tags oder der Zielordner geaendert?"""
        if os.path.normcase(self.folder) != os.path.normcase(os.path.dirname(self.src)):
            return True
        dflt = self._defaults(1, 1)
        return any(text != dflt[c - 3] for c, text in self._edits.get(1, {}).items())

    def _save_label(self, kept: int) -> str:
        if not self.wave.splits and not self.wave.removed:
            return "Änderungen speichern"
        if kept == 1:
            return "1 gekürzte Datei speichern" if self.wave.removed else "1 Datei speichern"
        return f"{kept} Teilstücke speichern"

    def _on_item_changed(self, item):
        if self._filling:
            return
        if item.column() == self.save_col:
            # erst nach dem Signal umbauen: _toggle_part ersetzt die Tabellenzeilen samt diesem Item
            row, keep = item.row(), item.checkState() == Qt.Checked
            QTimer.singleShot(0, lambda: self._toggle_part(row, keep))
            return
        self._edits.setdefault(item.row() + 1, {})[item.column()] = item.text()
        if not self.wave.splits and not self.wave.removed:
            self.b_save.setEnabled(self._meta_changed())

    def _part_span(self, row: int) -> tuple[float, float]:
        bounds = [0.0] + sorted(s for s in self.wave.splits if 0.0 < s < self.wave.duration) + [self.wave.duration]
        return bounds[row], bounds[row + 1]

    def _toggle_part(self, row: int, keep: bool):
        """Haken aus: ganzes Teilstueck als entfernt markieren. Haken an: seine entfernten Bereiche
        wieder freigeben (der Rest der Datei bleibt, wie er ist)."""
        a, b = self._part_span(row)
        self._push_undo()
        if keep:
            rest = []
            for x, y in self.wave.removed:
                if y <= a or x >= b:
                    rest.append((x, y))
                    continue
                if x < a:
                    rest.append((x, a))
                if y > b:
                    rest.append((b, y))
            self.wave.removed = rest
            self._changed()
        else:
            self.wave.sel = (a, b)
            self._cut()  # fasst mit vorhandenen entfernten Bereichen zusammen
            self._undo.pop()  # _cut hat einen zweiten Rueckgaengig-Schritt angelegt

    def _choose_folder(self):
        # Qt-eigener Dialog statt Windows-Ordnerauswahl: zeigt auch die Dateien im Ordner
        dlg = QFileDialog(self, "Zielordner wählen", self.folder)
        dlg.setFileMode(QFileDialog.Directory)
        dlg.setOption(QFileDialog.DontUseNativeDialog, True)
        dlg.setOption(QFileDialog.ShowDirsOnly, False)
        dlg.setNameFilters(["Audiodateien (" + " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS)) + ")",
                            "Alle Dateien (*)"])
        for lbl, text in ((QFileDialog.Accept, "Ordner wählen"), (QFileDialog.Reject, "Abbrechen"),
                          (QFileDialog.LookIn, "Suchen in:"), (QFileDialog.FileName, "Ordner:"),
                          (QFileDialog.FileType, "Dateityp:")):
            dlg.setLabelText(lbl, text)
        dlg.setViewMode(QFileDialog.Detail)
        dlg.resize(1000, 600)
        tree = dlg.findChild(QTreeView, "treeView")
        if tree is not None:
            tree.setColumnWidth(0, 480)
        self._folder_dialog = dlg  # fuer Tests erreichbar
        d = dlg.selectedFiles()[0] if dlg.exec() and dlg.selectedFiles() else ""
        if d and not os.path.isdir(d):
            d = os.path.dirname(d)
        if d:
            self.folder = os.path.normpath(d)
            self._update_folder_label()
            self._rebuild_parts()

    def _update_folder_label(self):
        self.lbl_folder.setText(f"Zielordner: {self.folder}")

    # ------------------------------------------------------------ Speichern
    def _save(self):
        if not self.wave.peaks.shape[0]:
            return
        parts = self._parts()
        if not self.wave.splits and not self.wave.removed:
            self._save_meta()
            return
        jobs = []
        ext = os.path.splitext(self.src)[1]
        for i, ranges in enumerate(parts, 1):
            if not ranges:
                continue
            vals = [self.table.item(i - 1, c).text() for c in range(3, len(self.COLS))]
            name = vals[0].strip()
            if not name:
                QMessageBox.warning(self, "Speichern", f"Teil {i}: Dateiname fehlt.")
                return
            if not name.lower().endswith(ext.lower()):
                name += ext
            tags = {key: vals[k + 1] for k, (key, _) in enumerate(editing.TAG_FIELDS)}
            jobs.append((i, ranges, os.path.join(self.folder, name), tags))
        dsts = [os.path.normcase(j[2]) for j in jobs]
        if len(set(dsts)) != len(dsts):
            QMessageBox.warning(self, "Speichern", "Zwei Teilstücke haben denselben Dateinamen.")
            return
        in_place = os.path.normcase(os.path.abspath(self.src)) in dsts
        clash = [j[2] for j in jobs if os.path.exists(j[2])
                 and os.path.normcase(j[2]) != os.path.normcase(os.path.abspath(self.src))]
        if clash:
            QMessageBox.warning(self, "Speichern", "Diese Dateien gibt es schon:\n" + "\n".join(clash))
            return
        keep = self.chk_keep.isChecked()
        src = os.path.abspath(self.src)
        old_name = self._defect_path(src, dsts)
        msg = "\n".join(f"Teil {i}: {os.path.basename(d)}  ({editing.part_duration(r):.1f} s)" for i, r, d, _ in jobs)
        where = "bleibt im Originalordner" if keep else "wandert in den Windows-Papierkorb"
        msg += f"\n\nDas Original wird in »{os.path.basename(old_name)}« umbenannt und {where}."
        if QMessageBox.question(self, self._save_label(len(jobs)), msg + "\n\nSpeichern?") != QMessageBox.Yes:
            return
        self.player.stop()
        written, temps, renamed = [], [], False
        try:
            for i, ranges, dst, tags in jobs:
                target = dst
                if os.path.normcase(dst) == os.path.normcase(src):
                    target = dst + ".neu" + ext   # Original erst nach dem Schreiben ersetzen
                    temps.append((target, dst))
                editing.write_part(self.src, target, ranges, tags)
                written.append(dst)
            os.rename(src, old_name)
            renamed = True
            if not keep:
                from send2trash import send2trash
                send2trash(old_name)
            for tmp, dst in temps:
                os.replace(tmp, dst)
        except Exception as exc:
            for tmp, _ in temps:
                if os.path.exists(tmp):
                    os.remove(tmp)
            if renamed and os.path.exists(old_name) and not os.path.exists(src):
                os.rename(old_name, src)
            QMessageBox.critical(self, "Speichern", f"Fehler beim Schreiben:\n{exc}")
            return
        # Keine zweite Bestaetigung: die Abfrage oben nennt schon, wohin das Original geht
        self.saved.emit(written, self.src, old_name if keep else "")
        self.close()

    def _save_meta(self):
        """Kein Schnitt: nur Tags schreiben und/oder die Datei umbenennen bzw. verschieben."""
        vals = [self.table.item(0, c).text() for c in range(3, len(self.COLS))]
        ext = os.path.splitext(self.src)[1]
        name = vals[0].strip()
        if not name:
            QMessageBox.warning(self, "Speichern", "Dateiname fehlt.")
            return
        if not name.lower().endswith(ext.lower()):
            name += ext
        src = os.path.abspath(self.src)
        dst = os.path.join(self.folder, name)
        tags = {key: vals[k + 1] for k, (key, _) in enumerate(editing.TAG_FIELDS)}
        changed_tags = {k: v for k, v in tags.items() if v.strip() != self.orig_tags.get(k, "").strip()}
        moved = os.path.normcase(dst) != os.path.normcase(src)
        if not changed_tags and not moved:
            QMessageBox.information(self, "Speichern", "Nichts geändert.")
            return
        if moved and os.path.exists(dst) and os.path.normcase(os.path.realpath(dst)) != os.path.normcase(src):
            QMessageBox.warning(self, "Speichern", f"Diese Datei gibt es schon:\n{dst}")
            return
        msg = []
        if changed_tags:
            labels = dict(editing.TAG_FIELDS)
            msg.append("Tags: " + ", ".join(f"{labels[k]} »{v}«" for k, v in changed_tags.items()))
        if moved:
            msg.append(f"Neuer Name:\n{dst}")
        if QMessageBox.question(self, "Änderungen speichern", "\n\n".join(msg) + "\n\nSpeichern?") != QMessageBox.Yes:
            return
        self.player.stop()
        try:
            if changed_tags:
                editing.write_tags(src, changed_tags)
            if moved:
                shutil.move(src, dst)
        except Exception as exc:
            QMessageBox.critical(self, "Speichern", f"Fehler beim Schreiben:\n{exc}")
            return
        self.retagged.emit(src, dst if moved else src)
        self.close()

    @staticmethod
    def _defect_path(src: str, taken: list[str]) -> str:
        """Neuer Name fuers Original: »(Original) Name«, bei Kollision mit Zaehler."""
        folder, base = os.path.split(src)
        stem, ext = os.path.splitext(base)
        n = 1
        while True:
            name = (f"{EditorWindow.ORIG_PREFIX} {base}" if n == 1
                    else f"{EditorWindow.ORIG_PREFIX} {stem} ({n}){ext}")
            path = os.path.join(folder, name)
            if not os.path.exists(path) and os.path.normcase(path) not in taken:
                return path
            n += 1

    def closeEvent(self, e):
        self.player.stop()
        if self._loader.isRunning():
            self._loader.wait(2000)
        super().closeEvent(e)
