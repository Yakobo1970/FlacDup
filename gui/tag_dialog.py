"""Tag-Abgleich A/B: alle Tags beider Dateien nebeneinander -- Cover, Titel, Interpret, Album, Komponist,
Kommentar, Liedtext, Dateiname. Pfeile uebernehmen ein Feld von einer Seite auf die andere; abweichende
Felder stehen in roter Schrift. Geschrieben wird erst mit "Speichern" (und nur, was sich geaendert hat)."""
from __future__ import annotations

import os

from PySide6.QtCore import QBuffer, QIODevice, QSize, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QToolButton,
                               QVBoxLayout, QWidget)

from engine import tagsync
from lang import T

from . import theme
from .widgets import BLUE_BUTTON, GREEN_BUTTON

COVER = 190
RED = theme.MISMATCH_TEXT.name()
CHANGED_STYLE = "border: 1px solid #d9a23a;"


def _cover_info(data: bytes | None, folder: bool) -> str:
    if not data:
        return T("kein Cover")
    img = QImage.fromData(data)
    kb = f"{len(data) / 1024:.0f} KB"
    fmt = "PNG" if data[:4] == b"\x89PNG" else "JPEG" if data[:2] == b"\xff\xd8" else T("Bild")
    size = f"{img.width()}×{img.height()}" if not img.isNull() else "?"
    text = f"{size} · {fmt} · {kb}"
    return text + "\n" + T("(nur Ordnerbild, nicht eingebettet)") if folder else text


class CoverBox(QWidget):
    """Coverbild einer Seite mit Groesse/Format und Knoepfen Laden/Entfernen."""
    changed = Signal()

    def __init__(self, data: bytes | None, folder: bool, writable: bool, parent=None):
        super().__init__(parent)
        self.orig, self.orig_folder = data, folder
        self.data, self.folder = data, folder
        self.writable = writable
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.img = QLabel()
        self.img.setFixedSize(COVER, COVER)
        self.img.setAlignment(Qt.AlignCenter)
        self.img.setStyleSheet("background:#1c1e22; border:1px solid #3a3c40;")
        self.info = QLabel()
        self.info.setStyleSheet("color:#999")
        row = QHBoxLayout()
        self.btn_load = QPushButton(T("Bild laden …"))
        self.btn_load.setToolTip(T("Ein Bild (JPEG, PNG, BMP, WebP) als Frontcover einbetten"))
        self.btn_del = QPushButton(T("Entfernen"))
        self.btn_del.setToolTip(T("Eingebettetes Frontcover entfernen"))
        self.btn_load.clicked.connect(self._load)
        self.btn_del.clicked.connect(lambda: self.set_data(None, False))
        row.addWidget(self.btn_load)
        row.addWidget(self.btn_del)
        row.addStretch(1)
        lay.addWidget(self.img)
        lay.addWidget(self.info)
        lay.addLayout(row)
        if not writable:
            self.btn_load.setEnabled(False)
            self.btn_del.setEnabled(False)
        self._show()

    def set_data(self, data: bytes | None, folder: bool):
        self.data, self.folder = data, folder
        self._show()
        self.changed.emit()

    def is_changed(self) -> bool:
        """Eingebettetes Cover soll sich aendern (ein uebernommenes Ordnerbild wird eingebettet)."""
        if self.folder:            # weiterhin nur Ordnerbild -> in der Datei aendert sich nichts
            return False
        if self.orig_folder:       # vorher nur Ordnerbild, jetzt eingebettet oder entfernt
            return self.data is not None
        return not tagsync.same_cover(self.data, self.orig)

    def _show(self):
        pm = QPixmap()
        if self.data and pm.loadFromData(self.data):
            self.img.setPixmap(pm.scaled(QSize(COVER, COVER), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.img.setPixmap(QPixmap())
            self.img.setText(T("kein Cover"))
        self.info.setText(_cover_info(self.data, self.folder))
        self.btn_del.setEnabled(self.writable and bool(self.data) and not self.folder)
        self.img.setStyleSheet("background:#1c1e22; border:%s;" % (
            "1px solid #d9a23a" if self.is_changed() else "1px solid #3a3c40"))

    def _load(self):
        path, _ = QFileDialog.getOpenFileName(self, T("Coverbild wählen"), os.path.dirname(self.window().paths[0]),
                                              T("Bilder") + " (*.jpg *.jpeg *.png *.bmp *.webp)")
        if not path:
            return
        with open(path, "rb") as fh:
            data = fh.read()
        if not (data[:2] == b"\xff\xd8" or data[:8] == b"\x89PNG\r\n\x1a\n"):   # andere Formate als JPEG einbetten
            img = QImage.fromData(data)
            if img.isNull():
                QMessageBox.warning(self, "FlacDup", T("Bild konnte nicht gelesen werden:\n{path}", path=path))
                return
            buf = QBuffer()
            buf.open(QIODevice.WriteOnly)
            img.save(buf, "JPG", 92)
            data = bytes(buf.data())
        self.set_data(data, False)


class TagCompareDialog(QDialog):
    def __init__(self, path_a: str, path_b: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(T("Tag-Abgleich A/B"))
        self.paths = [path_a, path_b]
        self.tags = [tagsync.read(path_a), tagsync.read(path_b)]
        self.fields: list[dict[str, QLineEdit | QPlainTextEdit]] = [{}, {}]
        self.labels: dict[str, QLabel] = {}
        self.stems: list[QLineEdit] = []
        self.arrows: dict[str, tuple[QToolButton, QToolButton]] = {}

        outer = QVBoxLayout(self)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        g = QGridLayout(inner)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(5)
        g.setColumnStretch(1, 1)
        g.setColumnStretch(3, 1)
        area.setWidget(inner)
        outer.addWidget(area, 1)

        # Kopf: Seite + Datei
        for side in (0, 1):
            head = QLabel(f"<b style='font-size:13pt'>{'AB'[side]}</b>&nbsp;&nbsp;"
                          f"<span style='color:#999'>{os.path.dirname(self.paths[side])}</span>")
            head.setToolTip(self.paths[side])
            head.setTextInteractionFlags(Qt.TextSelectableByMouse)
            g.addWidget(head, 0, 1 + 2 * side)
            if not self.tags[side]["writable"]:
                note = QLabel(T("Tags dieses Formats nur lesbar"))
                note.setStyleSheet(f"color:{RED}")
                g.addWidget(note, 1, 1 + 2 * side)
        row = 2

        # Cover
        self.covers = [CoverBox(self.tags[s]["cover"], self.tags[s]["cover_folder"],
                                self.tags[s]["cover_writable"]) for s in (0, 1)]
        self.labels["cover"] = QLabel(T("Cover"))
        g.addWidget(self.labels["cover"], row, 0, Qt.AlignTop)
        g.addWidget(self.covers[0], row, 1)
        g.addWidget(self._arrows("cover"), row, 2, Qt.AlignVCenter)
        g.addWidget(self.covers[1], row, 3)
        for c in self.covers:
            c.changed.connect(self._update)
        row += 1

        # Dateiname (ohne Endung)
        self.labels["stem"] = QLabel(T("Dateiname"))
        g.addWidget(self.labels["stem"], row, 0)
        for side in (0, 1):
            stem, ext = os.path.splitext(os.path.basename(self.paths[side]))
            box = QHBoxLayout()
            e = QLineEdit(stem)
            e.setProperty("orig", stem)
            e.textChanged.connect(self._update)
            self.stems.append(e)
            box.addWidget(e, 1)
            lab = QLabel(ext)
            lab.setStyleSheet("color:#999")
            box.addWidget(lab)
            w = QWidget()
            w.setLayout(box)
            box.setContentsMargins(0, 0, 0, 0)
            g.addWidget(w, row, 1 + 2 * side)
        g.addWidget(self._arrows("stem"), row, 2)
        row += 1

        # Tag-Felder
        for key, name in tagsync.FIELDS:
            lab = QLabel(name)
            self.labels[key] = lab
            g.addWidget(lab, row, 0, Qt.AlignTop if key == "lyrics" else Qt.AlignVCenter)
            for side in (0, 1):
                val = self.tags[side]["values"].get(key, "")
                if key == "lyrics":
                    e = QPlainTextEdit(val)
                    e.setFixedHeight(130)
                    e.textChanged.connect(self._update)
                else:
                    e = QLineEdit(val)
                    e.setCursorPosition(0)
                    e.textChanged.connect(self._update)
                # Vergleichswert = Text, wie ihn das Feld zeigt (Zeilenumbrueche \r\n -> \n), sonst gaelte
                # ein unveraenderter Liedtext als geaendert
                e.setProperty("orig", self._text(e))
                e.setReadOnly(not self.tags[side]["writable"])
                self.fields[side][key] = e
                g.addWidget(e, row, 1 + 2 * side)
            g.addWidget(self._arrows(key), row, 2, Qt.AlignTop if key == "lyrics" else Qt.AlignVCenter)
            row += 1

        # Knoepfe
        bar = QHBoxLayout()
        b_fill = QPushButton(T("Leere Felder ergänzen"))
        b_fill.setToolTip(T("Felder, die nur auf einer Seite ausgefüllt sind, auf die andere Seite übernehmen "
                            "(auch das Cover). Ausgefüllte Felder bleiben unverändert."))
        b_fill.clicked.connect(self._fill_empty)
        b_ab = QPushButton(T("Alles A → B"))
        b_ab.setToolTip(T("Alle Tags und das Cover von A nach B übernehmen (Dateiname nicht)"))
        b_ab.clicked.connect(lambda: self._copy_all(0))
        b_ba = QPushButton(T("Alles B → A"))
        b_ba.setToolTip(T("Alle Tags und das Cover von B nach A übernehmen (Dateiname nicht)"))
        b_ba.clicked.connect(lambda: self._copy_all(1))
        b_reset = QPushButton(T("Zurücksetzen"))
        b_reset.setToolTip(T("Alle Änderungen in diesem Fenster verwerfen"))
        b_reset.clicked.connect(self._reset)
        self.lbl_sum = QLabel()
        self.btn_save = QPushButton(T("Speichern"))
        self.btn_save.setStyleSheet(GREEN_BUTTON)
        self.btn_save.setToolTip(T("Geänderte Tags in die Dateien schreiben – mit Strg+Z im Hauptfenster rückgängig"))
        self.btn_save.clicked.connect(self._save)
        b_cancel = QPushButton(T("Abbrechen"))
        b_cancel.clicked.connect(self.reject)
        for b in (b_fill, b_ab, b_ba, b_reset):
            b.setStyleSheet(BLUE_BUTTON) if b is b_fill else None
            bar.addWidget(b)
        bar.addSpacing(12)
        bar.addWidget(self.lbl_sum, 1)
        bar.addWidget(b_cancel)
        bar.addWidget(self.btn_save)
        outer.addLayout(bar)
        hint = QLabel(T("Rot = A und B weichen ab · Orange umrandet = geändert, noch nicht gespeichert · "
                        "> übernimmt A nach B, < übernimmt B nach A"))
        hint.setStyleSheet("color:#999")
        outer.addWidget(hint)
        self.resize(1180, 860)
        self._update()

    # ------------------------------------------------------------ Bausteine
    def _arrows(self, key: str) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w) if key in ("cover", "lyrics") else QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        pair = []
        for src, text, tip in ((0, ">", T("A nach B übernehmen")), (1, "<", T("B nach A übernehmen"))):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setFixedSize(28, 24)
            f = b.font()
            f.setBold(True)
            b.setFont(f)
            sp = b.sizePolicy()
            sp.setRetainSizeWhenHidden(True)      # ausgeblendeter Pfeil laesst seinen Platz frei
            b.setSizePolicy(sp)
            b.clicked.connect(lambda _=False, k=key, s=src: self._copy(k, s))
            dst = 1 - src
            if (key == "cover" and not self.tags[dst]["cover_writable"]) or \
                    (key not in ("cover", "stem") and not self.tags[dst]["writable"]):
                b.setEnabled(False)
            lay.addWidget(b)
            pair.append(b)
        self.arrows[key] = (pair[0], pair[1])
        return w

    @staticmethod
    def _text(e) -> str:
        return e.toPlainText() if isinstance(e, QPlainTextEdit) else e.text()

    @staticmethod
    def _set_text(e, v: str):
        if isinstance(e, QPlainTextEdit):
            e.setPlainText(v)
        else:
            e.setText(v)
            e.setCursorPosition(0)

    def _copy(self, key: str, src: int):
        dst = 1 - src
        if key == "cover":
            c = self.covers[src]
            if self.covers[dst].writable:
                self.covers[dst].set_data(c.data, False)
        elif key == "stem":
            self.stems[dst].setText(self.stems[src].text())
        elif not self.fields[dst][key].isReadOnly():
            self._set_text(self.fields[dst][key], self._text(self.fields[src][key]))

    def _copy_all(self, src: int):
        for key in tagsync.KEYS:
            self._copy(key, src)
        self._copy("cover", src)

    def _fill_empty(self):
        for key in tagsync.KEYS:
            va, vb = (self._text(self.fields[s][key]).strip() for s in (0, 1))
            if va and not vb:
                self._copy(key, 0)
            elif vb and not va:
                self._copy(key, 1)
        ca, cb = self.covers
        if ca.data and not cb.data:
            self._copy("cover", 0)
        elif cb.data and not ca.data:
            self._copy("cover", 1)

    def _reset(self):
        for side in (0, 1):
            for key, e in self.fields[side].items():
                self._set_text(e, e.property("orig"))
            self.stems[side].setText(self.stems[side].property("orig"))
            c = self.covers[side]
            c.set_data(c.orig, c.orig_folder)

    def _update(self):
        """Abweichende Inhalte rot, Geaendertes orange umrandet; Pfeile nur dort, wo die Quelle etwas enthaelt."""
        n = 0
        for key in tagsync.KEYS:
            vals = [self._text(self.fields[s][key]).strip() for s in (0, 1)]
            for s in (0, 1):
                e = self.fields[s][key]
                changed = self._text(e) != e.property("orig")
                n += changed
                e.setStyleSheet(self._style(vals[0] != vals[1], changed))
            self._show_arrows(key, vals)
        stems = [e.text().strip() for e in self.stems]
        for e in self.stems:
            changed = e.text() != e.property("orig")
            n += changed
            e.setStyleSheet(self._style(stems[0] != stems[1], changed))
        self._show_arrows("stem", stems)
        differ = not tagsync.same_cover(self.covers[0].data, self.covers[1].data)
        for c in self.covers:
            n += c.is_changed()
            c._show()
            c.info.setStyleSheet(f"color:{RED}; font-weight:bold;" if differ else "color:#999")
        self._show_arrows("cover", [c.data for c in self.covers])
        self.lbl_sum.setText(T("{n} Änderung(en)", n=n) if n else "")
        self.btn_save.setEnabled(n > 0)

    @staticmethod
    def _style(differ: bool, changed: bool) -> str:
        return (f"color:{RED}; font-weight:bold;" if differ else "") + (CHANGED_STYLE if changed else "")

    def _show_arrows(self, key: str, vals):
        """> nur, wenn A etwas enthaelt, < nur, wenn B etwas enthaelt."""
        for src, b in enumerate(self.arrows[key]):
            b.setVisible(bool(vals[src]))

    # ------------------------------------------------------------ Ergebnis
    def changes(self) -> dict[int, dict]:
        """{seite: {'values': {feld: text}, 'cover': KEEP|None|bytes, 'stem': neuer Name oder None}}"""
        out = {}
        for side in (0, 1):
            vals = {k: self._text(e) for k, e in self.fields[side].items() if self._text(e) != e.property("orig")}
            c = self.covers[side]
            cover = c.data if c.is_changed() else tagsync.KEEP
            stem = self.stems[side].text().strip()
            stem = stem if stem != self.stems[side].property("orig") else None
            if vals or cover is not tagsync.KEEP or stem:
                out[side] = {"values": vals, "cover": cover, "stem": stem}
        return out

    def _save(self):
        ch = self.changes()
        for side, c in ch.items():
            if c["stem"] is None:
                continue
            if not tagsync.safe_stem(c["stem"]):
                QMessageBox.warning(self, "FlacDup", T("Ungültiger Dateiname: {name}", name=c["stem"]))
                return
            new = os.path.join(os.path.dirname(self.paths[side]), c["stem"] + os.path.splitext(self.paths[side])[1])
            if os.path.exists(new) and os.path.normcase(new) != os.path.normcase(self.paths[side]):
                QMessageBox.warning(self, "FlacDup", T("Datei gibt es schon:\n{path}", path=new))
                return
        self.accept()
