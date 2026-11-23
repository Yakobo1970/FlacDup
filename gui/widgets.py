"""Kleine eigene Bedienelemente des Hauptfensters."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QPushButton

BLUE_BUTTON = """
QPushButton { background: #2d6cb0; color: white; font-weight: bold; border: 1px solid #4a86c8;
              border-radius: 6px; padding: 4px 10px; }
QPushButton:hover { background: #3f83cc; }
QPushButton:pressed { background: #24588f; }
QPushButton:disabled { background: #3a3c40; color: #777; border-color: #45474c; }
"""
GREEN_BUTTON = """
QPushButton { background: #2f7d46; color: white; font-weight: bold; font-size: 11pt; border: 1px solid #45a061;
              border-radius: 6px; padding: 4px 14px; }
QPushButton:hover { background: #3a9656; }
QPushButton:pressed { background: #276a3b; }
QPushButton:disabled { background: #3a3c40; color: #777; border-color: #45474c; }
"""


def trash_icon(size: int = 64, color: QColor = QColor(255, 255, 255)) -> QIcon:
    """Papierkorb-Symbol (Deckel mit Griff, Korb mit drei Rillen), selbst gezeichnet."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 24.0
    pen = QPen(color, 1.9 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.drawLine(QPointF(4 * s, 6 * s), QPointF(20 * s, 6 * s))                        # Deckel
    p.drawRoundedRect(QRectF(9 * s, 3 * s, 6 * s, 3 * s), 1 * s, 1 * s)              # Griff
    body = QPainterPath(QPointF(6 * s, 6 * s))                                        # Korb
    body.lineTo(7.2 * s, 20.5 * s)
    body.lineTo(16.8 * s, 20.5 * s)
    body.lineTo(18 * s, 6 * s)
    p.drawPath(body)
    for x in (10, 12, 14):                                                            # Rillen
        p.drawLine(QPointF(x * s, 9.5 * s), QPointF(x * s, 17.5 * s))
    p.end()
    return QIcon(pm)


class TrashButton(QPushButton):
    """Roter Papierkorb-Knopf mit dem Coverbild der Datei darunter (eingepasst, nicht gestreckt);
    meldet Maus darueber, damit die betroffene Welle markiert wird."""
    hovered = Signal(bool)
    SIZE = 136

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.PointingHandCursor)
        self._icon = trash_icon(64).pixmap(64, 64)
        self._cover: QPixmap | None = None
        self._hover = False

    def set_cover(self, data: bytes | None):
        pm = QPixmap()
        self._cover = pm if data and pm.loadFromData(data) and not pm.isNull() else None
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        on = self.isEnabled()
        r = QRectF(self.rect())
        if self._cover is not None:
            # Cover nutzt das ganze Feld: eingepasst, Seitenverhaeltnis bleibt, kein Rahmen
            pm = self._cover.scaled(int(r.width()), int(r.height()), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            area = QRectF(r.x() + (r.width() - pm.width()) / 2, r.y() + (r.height() - pm.height()) / 2,
                          pm.width(), pm.height())
            p.setOpacity(1.0 if on else 0.35)
            p.drawPixmap(area.topLeft(), pm)
            p.setOpacity(1.0)
        else:                                   # ohne Cover: schlichtes rotes Feld
            area = r
            p.fillRect(area, QColor("#8e3a34") if on else QColor("#3a3c40"))
        if on and self._hover:
            p.fillRect(area, QColor(226, 103, 94, 120))
        # Papierkorb-Symbol in der Mitte auf rotem Rund, damit es auf jedem Cover zu sehen ist
        c = r.center()
        badge = 23
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#e2675e") if (on and self._hover) else QColor(142, 58, 52, 215) if on
                   else QColor(70, 72, 76, 215))
        p.drawEllipse(c, badge, badge)
        icon = self._icon.scaled(30, 30, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        p.drawPixmap(QPointF(c.x() - icon.width() / 2, c.y() - icon.height() / 2), icon)
        p.end()

    def enterEvent(self, ev):
        self._hover = True
        self.update()
        if self.isEnabled():
            self.hovered.emit(True)
        super().enterEvent(ev)

    def leaveEvent(self, ev):
        self._hover = False
        self.update()
        self.hovered.emit(False)
        super().leaveEvent(ev)

    def setEnabled(self, on: bool):
        if not on:
            self.hovered.emit(False)
        super().setEnabled(on)
