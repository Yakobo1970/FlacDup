"""Fenster 'Nicht lesbare Dateien' (Menue Datei): Liste mit Grund, Sprung in den Explorer,
erneut einlesen. Bewusst nicht im Hauptfenster -- solche Dateien sollten selten sein."""
from __future__ import annotations

import os
import subprocess

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from lang import T


class UnreadableDialog(QDialog):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.retried = 0
        self.setWindowTitle(T("Nicht lesbare Dateien"))
        self.resize(1100, 420)
        lay = QVBoxLayout(self)
        self.info = QLabel()
        self.info.setWordWrap(True)
        lay.addWidget(self.info)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels([T("Datei"), T("Ordner"), T("Grund")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Interactive)
        hh.setStretchLastSection(True)
        self.table.setColumnWidth(0, 340)
        self.table.setColumnWidth(1, 420)
        self.table.doubleClicked.connect(lambda _i: self._explorer())
        lay.addWidget(self.table, 1)

        bar = QHBoxLayout()
        self.b_explorer = QPushButton(T("Im Explorer zeigen"))
        self.b_explorer.setToolTip(T("Öffnet den Ordner und markiert die Datei (auch per Doppelklick)"))
        self.b_explorer.clicked.connect(self._explorer)
        self.b_retry = QPushButton(T("Erneut einlesen"))
        self.b_retry.setToolTip(T("Gewählte Dateien beim nächsten „Analyse fortsetzen“ noch einmal analysieren"))
        self.b_retry.clicked.connect(self._retry)
        close = QPushButton(T("Schließen"))
        close.setDefault(True)
        close.clicked.connect(self.accept)
        bar.addWidget(self.b_explorer)
        bar.addWidget(self.b_retry)
        bar.addStretch(1)
        bar.addWidget(close)
        lay.addLayout(bar)
        self._fill()

    def _fill(self):
        rows = self.db.unreadable()
        self.table.setRowCount(len(rows))
        for r, (fid, path, err) in enumerate(rows):
            folder, name = os.path.split(path)
            for c, text in enumerate((name, folder, err)):
                item = QTableWidgetItem(text)
                item.setToolTip(path if c < 2 else text)
                item.setData(Qt.UserRole, (fid, path))
                self.table.setItem(r, c, item)
        self.info.setText(T("{n} Datei(en) ließen sich nicht lesen und fehlen deshalb im Abgleich.", n=len(rows))
                          if rows else T("Alle Dateien ließen sich lesen."))
        self.b_explorer.setEnabled(bool(rows))
        self.b_retry.setEnabled(bool(rows))
        if rows:
            self.table.selectAll()

    def _selected(self) -> list[tuple[int, str]]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r, 0).data(Qt.UserRole) for r in rows]

    def _explorer(self):
        for _fid, path in self._selected()[:5]:     # nicht versehentlich Dutzende Fenster oeffnen
            if os.path.exists(path):
                subprocess.Popen(["explorer", "/select,", path])
            elif os.path.isdir(os.path.dirname(path)):
                os.startfile(os.path.dirname(path))

    def _retry(self):
        ids = [fid for fid, _ in self._selected()]
        if not ids:
            return
        self.db.retry(ids)
        self.retried += len(ids)
        self._fill()
        self.info.setText(T("{n} Datei(en) werden beim nächsten „Analyse fortsetzen“ erneut eingelesen.", n=len(ids))
                          + (" " + T("Noch {n} weitere nicht lesbar.", n=self.table.rowCount())
                             if self.table.rowCount() else ""))
