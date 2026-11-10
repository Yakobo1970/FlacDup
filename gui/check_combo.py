"""Klappliste mit Haekchen: Mehrfachauswahl, erster Eintrag "Alle ..." schaltet alle."""
from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QComboBox, QStyle, QStyledItemDelegate, QStyleOptionComboBox,
                               QStyleOptionViewItem, QStylePainter)


class CheckCombo(QComboBox):
    selection_changed = Signal()

    def __init__(self, all_label: str, noun: str, parent=None):
        super().__init__(parent)
        self.all_label = all_label
        self.noun = noun                      # fuer die Zusammenfassung "2 von 5 <noun>"
        self._model = QStandardItemModel(self)
        self.setModel(self._model)
        self._updating = False
        all_item = QStandardItem(all_label)
        all_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
        all_item.setData(Qt.Checked, Qt.CheckStateRole)
        self._model.appendRow(all_item)
        self._model.itemChanged.connect(self._on_item_changed)
        # Standard-Zeichner statt des Menue-Stils der Klappliste: nur so liegt das gezeichnete
        # Kaestchen genau dort, wo _in_check_box es berechnet
        self.setItemDelegate(QStyledItemDelegate(self))
        self.view().viewport().installEventFilter(self)
        self.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self._refresh_text()

    # ------------------------------------------------------------ Eintraege
    def set_items(self, items: list[tuple[str, object]], checked: set | None = None):
        """items: (Anzeigetext, Wert). checked=None -> alle angehakt."""
        self._updating = True
        while self._model.rowCount() > 1:
            self._model.removeRow(1)
        for text, value in items:
            it = QStandardItem(text)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            it.setData(value, Qt.UserRole)
            it.setToolTip(str(value))
            on = checked is None or value in checked
            it.setData(Qt.Checked if on else Qt.Unchecked, Qt.CheckStateRole)
            self._model.appendRow(it)
        self._sync_all_item()
        self._updating = False
        self._refresh_text()

    def values(self) -> list:
        return [self._model.item(i).data(Qt.UserRole) for i in range(1, self._model.rowCount())]

    def checked_values(self) -> list:
        return [self._model.item(i).data(Qt.UserRole) for i in range(1, self._model.rowCount())
                if self._model.item(i).checkState() == Qt.Checked]

    def all_checked(self) -> bool:
        return len(self.checked_values()) == len(self.values())

    def set_checked(self, values: set):
        self._updating = True
        for i in range(1, self._model.rowCount()):
            it = self._model.item(i)
            it.setCheckState(Qt.Checked if it.data(Qt.UserRole) in values else Qt.Unchecked)
        self._sync_all_item()
        self._updating = False
        self._refresh_text()
        self.selection_changed.emit()

    # ------------------------------------------------------------ Verhalten
    def eventFilter(self, obj, event):
        # Nur ein Klick (Druecken UND Loslassen in der offenen Liste) auf das Haekchenkaestchen
        # schaltet um. Das Loslassen nach dem Oeffnen der Liste und Klicks auf den Namen
        # aendern nichts; die Liste bleibt in beiden Faellen offen.
        if obj is self.view().viewport():
            if event.type() in (QEvent.MouseButtonPress, QEvent.MouseButtonDblClick):
                pos = event.position().toPoint()
                idx = self.view().indexAt(pos)
                self._press_index = idx if idx.isValid() and self._in_check_box(idx, pos) else None
                return True
            if event.type() == QEvent.MouseButtonRelease:
                pos = event.position().toPoint()
                idx = self.view().indexAt(pos)
                pressed, self._press_index = getattr(self, "_press_index", None), None
                if (pressed is not None and idx.isValid() and idx == pressed
                        and self._in_check_box(idx, pos)):
                    it = self._model.itemFromIndex(idx)
                    it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)
                return True
        return super().eventFilter(obj, event)

    def _in_check_box(self, idx, pos) -> bool:
        """Liegt pos im Haekchenkaestchen der Zeile idx (etwas grosszuegig fuer die Maus)?"""
        view = self.view()
        opt = QStyleOptionViewItem()
        opt.initFrom(view)
        opt.rect = view.visualRect(idx)
        opt.features = QStyleOptionViewItem.HasCheckIndicator | QStyleOptionViewItem.HasDisplay
        opt.text = idx.data(Qt.DisplayRole) or ""
        box = view.style().subElementRect(QStyle.SE_ItemViewItemCheckIndicator, opt, view)
        if not box.isValid():  # Fallback: Kaestchen am linken Zeilenrand
            r = opt.rect
            box = r.adjusted(0, 0, -(r.width() - r.height() - 4), 0)
        return box.adjusted(-4, -4, 4, 4).contains(pos)

    def _on_item_changed(self, item: QStandardItem):
        if self._updating:
            return
        self._updating = True
        if item.row() == 0:  # "Alle ..." setzt/entfernt alle Haekchen
            state = item.checkState()
            for i in range(1, self._model.rowCount()):
                self._model.item(i).setCheckState(state)
        self._sync_all_item()
        self._updating = False
        self._refresh_text()
        self.selection_changed.emit()

    def _sync_all_item(self):
        n = self._model.rowCount() - 1
        k = len(self.checked_values())
        state = Qt.Checked if n and k == n else (Qt.PartiallyChecked if k else Qt.Unchecked)
        self._model.item(0).setCheckState(state)

    def _refresh_text(self):
        checked = self.checked_values()
        n = len(self.values())
        if n and len(checked) == n:
            text = self.all_label
        elif not checked:
            text = f"Keine {self.noun}"
        elif len(checked) == 1:
            row = [i for i in range(1, self._model.rowCount())
                   if self._model.item(i).checkState() == Qt.Checked][0]
            text = self._model.item(row).text()
        else:
            text = f"{len(checked)} von {n} {self.noun}"
        self._summary = text
        self.setPlaceholderText(text)
        self.setCurrentIndex(-1)
        self.setToolTip(text)
        self.update()

    def paintEvent(self, event):
        # Zusammenfassung in normaler Schrift statt im grauen Platzhalter-Stil
        painter = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        opt.currentText = getattr(self, "_summary", "")
        painter.drawComplexControl(QStyle.CC_ComboBox, opt)
        painter.drawControl(QStyle.CE_ComboBoxLabel, opt)
