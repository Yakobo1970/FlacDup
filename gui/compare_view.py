"""Beide Wellenformen eines Paares untereinander auf einer gemeinsamen Zeitachse.

B wird so verschoben (und bei Tempo-Varianten gestreckt), dass gemeinsame Stellen
uebereinander liegen. Farbe je Stelle: gruen = klingt gleich, rot = verschieden,
grau = in der anderen Datei nicht vorhanden. Klick setzt die Abspielposition in
der angeklickten Datei; Mausrad zoomt, Ziehen verschiebt.
"""
from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from . import theme

LANE_GAP = 26
RULER_H = 18
LABEL_W = 34


def fmt_time(t: float) -> str:
    if t is None or t != t:
        return "–"
    sign = "-" if t < 0 else ""
    t = abs(t)
    return f"{sign}{int(t // 60)}:{t % 60:04.1f}"


class CompareView(QWidget):
    seek_requested = Signal(int, float)    # Seite, Sekunde in dieser Datei
    edit_requested = Signal(int, float)    # Rechtsklick: Seite im Editor oeffnen, Cursor an dieser Stelle

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self.env = [np.zeros(0, np.float32), np.zeros(0, np.float32)]
        self.dur = [0.0, 0.0]
        self.names = ["", ""]
        self.ts, self.off = 1.0, 0.0
        self.res: dict | None = None
        self.side = 0
        self.play_t = [0.0, 0.0]
        self.partner_t: float | None = None
        self.view = (0.0, 1.0)    # sichtbarer Bereich der A-Zeitachse
        self._drag: tuple[float, tuple[float, float]] | None = None
        self._press_x = 0.0
        self.message = "Paar in der Liste wählen"

    # ------------------------------------------------------------ Daten
    def clear(self, message: str = ""):
        self.env = [np.zeros(0, np.float32), np.zeros(0, np.float32)]
        self.dur = [0.0, 0.0]
        self.res = None
        self.message = message
        self.update()

    def set_pair(self, env_a, env_b, dur_a, dur_b, names, ts, off):
        self.env = [np.asarray(env_a, np.float32), np.asarray(env_b, np.float32)]
        self.dur = [dur_a or 0.0, dur_b or 0.0]
        self.names = names
        self.ts, self.off = ts or 1.0, off or 0.0
        self.play_t = [0.0, 0.0]
        self.partner_t = None
        self.message = ""
        self.view = self._full_range()
        self.update()

    def set_compare(self, res: dict | None):
        self.res = res
        self.update()

    def set_play(self, side: int, t: float, partner_t: float | None):
        self.side = side
        self.play_t[side] = t
        self.partner_t = partner_t
        self.update()

    # ------------------------------------------------------------ Zeitachse
    def to_axis(self, side: int, t: float) -> float:
        """Zeit in Datei side -> gemeinsame Achse (= Zeit in A)."""
        return t if side == 0 else (t - self.off) / self.ts

    def from_axis(self, side: int, x: float) -> float:
        return x if side == 0 else x * self.ts + self.off

    def _full_range(self) -> tuple[float, float]:
        b0, b1 = self.to_axis(1, 0.0), self.to_axis(1, self.dur[1])
        lo, hi = min(0.0, b0), max(self.dur[0], b1)
        pad = (hi - lo) * 0.01 + 0.1
        return lo - pad, hi + pad

    def _x(self, axis_t: float) -> float:
        lo, hi = self.view
        w = self.width() - LABEL_W
        return LABEL_W + (axis_t - lo) / max(1e-9, hi - lo) * w

    def _axis_at(self, x: float) -> float:
        lo, hi = self.view
        w = self.width() - LABEL_W
        return lo + (x - LABEL_W) / max(1, w) * (hi - lo)

    def _lanes(self) -> list[QRectF]:
        h = (self.height() - RULER_H * 2 - LANE_GAP) / 2
        top = QRectF(LABEL_W, RULER_H, self.width() - LABEL_W, h)
        bot = QRectF(LABEL_W, RULER_H + h + LANE_GAP, self.width() - LABEL_W, h)
        return [top, bot]

    def _sim_at(self, side: int, t: float) -> float | None:
        if self.res is None:
            return None
        sim = self.res["a_sim"] if side == 0 else self.res["b_sim"]
        if sim.size == 0:
            return None
        i = int(round((t - self.res["win"] / 2) / self.res["step"]))
        if i < 0 or i >= sim.size:
            return float("nan")
        return float(sim[i])

    # ------------------------------------------------------------ Zeichnen
    def paintEvent(self, _ev):
        p = QPainter(self)
        p.fillRect(self.rect(), self.palette().base())
        if self.message or not self.dur[0]:
            p.setPen(QColor(150, 150, 150))
            p.drawText(self.rect(), Qt.AlignCenter, self.message or "")
            return
        lanes = self._lanes()
        # Wellen und Lineale nur bei Aenderung neu zeichnen (Zwischenbild), die Abspiellinien
        # laufen live darueber -- sonst blockiert das Neuzeichnen alle 30 ms die Wiedergabe
        key = (self.width(), self.height(), self.view, id(self.res), self.side, id(self.env[0]),
               id(self.env[1]), self.ts, self.off)
        if getattr(self, "_bg_key", None) != key:
            from PySide6.QtGui import QPixmap
            bg = QPixmap(self.size() * self.devicePixelRatioF())
            bg.setDevicePixelRatio(self.devicePixelRatioF())
            bg.fill(self.palette().base().color())
            q = QPainter(bg)
            for side in (0, 1):
                self._draw_lane(q, side, lanes[side])
            self._draw_rulers(q, lanes)
            q.end()
            self._bg, self._bg_key = bg, key
        p.drawPixmap(0, 0, self._bg)
        self._draw_playheads(p, lanes)

    def _draw_lane(self, p: QPainter, side: int, r: QRectF):
        if side == self.side:
            p.fillRect(QRectF(0, r.top() - 2, self.width(), r.height() + 4), theme.ACTIVE_LANE)
        p.setPen(QColor(230, 230, 230) if side == self.side else QColor(150, 150, 150))
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSize(f.pointSize() + 3)
        p.setFont(f)
        p.drawText(QRectF(0, r.top(), LABEL_W, r.height()), Qt.AlignCenter, "AB"[side])
        p.setFont(self.font())
        env, dur = self.env[side], self.dur[side]
        if env.size == 0 or dur <= 0:
            return
        mid = r.center().y()
        half = r.height() / 2 - 2
        x0 = max(int(r.left()), int(self._x(self.to_axis(side, 0.0))))
        x1 = min(int(r.right()), int(self._x(self.to_axis(side, dur))))
        last_color = None
        for x in range(x0, x1 + 1):
            t = self.from_axis(side, self._axis_at(x))
            t2 = self.from_axis(side, self._axis_at(x + 1))
            i0 = int(np.clip(min(t, t2) / dur * env.size, 0, env.size - 1))
            i1 = int(np.clip(max(t, t2) / dur * env.size, i0 + 1, env.size))
            amp = float(env[i0:i1].max()) if i1 > i0 else float(env[i0])
            s = self._sim_at(side, (t + t2) / 2)
            color = theme.PENDING_WAVE if s is None else theme.sim_color(s)
            if color != last_color:
                p.setPen(color)
                last_color = color
            h = max(1.0, amp * half)
            p.drawLine(QPointF(x, mid - h), QPointF(x, mid + h))
        # Dateiname oben links in der Spur
        p.setPen(QColor(210, 210, 210))
        p.drawText(QRectF(r.left() + 6, r.top() + 2, r.width() - 12, 16),
                   Qt.AlignLeft | Qt.AlignTop, self.names[side])

    def _draw_rulers(self, p: QPainter, lanes):
        p.setPen(QColor(150, 150, 150))
        lo, hi = self.view
        span = hi - lo
        step = next((s for s in (1, 2, 5, 10, 15, 30, 60, 120, 300, 600) if span / s <= 12), 1200)
        for side, y, flip in ((0, lanes[0].top() - 2, -1), (1, lanes[1].bottom() + 2, 1)):
            t_lo = self.from_axis(side, lo)
            t_hi = self.from_axis(side, hi)
            t = np.ceil(min(t_lo, t_hi) / step) * step
            while t <= max(t_lo, t_hi):
                if 0 <= t <= self.dur[side]:
                    x = self._x(self.to_axis(side, t))
                    p.drawLine(QPointF(x, y), QPointF(x, y + 4 * flip))
                    ty = y - 14 if flip < 0 else y + 3
                    p.drawText(QRectF(x - 30, ty, 60, 12), Qt.AlignCenter, fmt_time(t))
                t += step

    def _draw_playheads(self, p: QPainter, lanes):
        other = 1 - self.side
        pts = []
        for side, t in ((self.side, self.play_t[self.side]), (other, self.partner_t)):
            if t is None:
                continue
            x = self._x(self.to_axis(side, t))
            r = lanes[side]
            pen = QPen(theme.PLAYHEAD if side == self.side else QColor(255, 255, 255, 120))
            pen.setWidthF(2.0 if side == self.side else 1.0)
            if side != self.side:
                pen.setStyle(Qt.DashLine)
            p.setPen(pen)
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            pts.append((side, x))
        if len(pts) == 2:   # Verbindung zur entsprechenden Stelle in der anderen Datei
            pen = QPen(QColor(255, 255, 255, 90))
            pen.setStyle(Qt.DotLine)
            p.setPen(pen)
            xa = dict(pts)[0]
            xb = dict(pts)[1]
            p.drawLine(QPointF(xa, lanes[0].bottom()), QPointF(xb, lanes[1].top()))

    # ------------------------------------------------------------ Maus
    def _side_at(self, y: float) -> int | None:
        lanes = self._lanes()
        for side in (0, 1):
            if lanes[side].adjusted(0, -LANE_GAP / 2, 0, LANE_GAP / 2).contains(QPointF(LABEL_W + 1, y)):
                return side
        return None

    def wheelEvent(self, ev):
        if not self.dur[0]:
            return
        lo, hi = self.view
        center = self._axis_at(ev.position().x())
        factor = 0.8 if ev.angleDelta().y() > 0 else 1.25
        full = self._full_range()
        new_span = min(full[1] - full[0], max(2.0, (hi - lo) * factor))
        frac = (center - lo) / max(1e-9, hi - lo)
        lo2 = center - frac * new_span
        self.view = self._clamp(lo2, lo2 + new_span)
        self.update()

    def _clamp(self, lo, hi):
        full = self._full_range()
        span = hi - lo
        if lo < full[0]:
            lo, hi = full[0], full[0] + span
        if hi > full[1]:
            lo, hi = full[1] - span, full[1]
        return lo, hi

    def mousePressEvent(self, ev):
        if ev.button() == Qt.RightButton and self.dur[0]:
            side = self._side_at(ev.position().y())
            if side is not None:
                t = self.from_axis(side, self._axis_at(ev.position().x()))
                self.edit_requested.emit(side, min(max(0.0, t), self.dur[side]))
            return
        if ev.button() == Qt.LeftButton:
            self._drag = (ev.position().x(), self.view)
            self._press_x = ev.position().x()

    def mouseMoveEvent(self, ev):
        if self._drag is not None and ev.buttons() & Qt.LeftButton:
            x0, (lo, hi) = self._drag
            dx = (ev.position().x() - x0) / max(1, self.width() - LABEL_W) * (hi - lo)
            if abs(ev.position().x() - self._press_x) > 4:
                self.view = self._clamp(lo - dx, hi - dx)
                self.update()
            return
        side = self._side_at(ev.position().y())
        if side is None or not self.dur[0]:
            QToolTip.hideText()
            return
        t = self.from_axis(side, self._axis_at(ev.position().x()))
        if 0 <= t <= self.dur[side]:
            s = self._sim_at(side, t)
            txt = f"{'AB'[side]} {fmt_time(t)}"
            if s is not None:
                txt += "  ·  nicht in der anderen Datei" if s != s else f"  ·  Übereinstimmung {s:.2f}"
            QToolTip.showText(ev.globalPosition().toPoint(), txt, self)

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton and self._drag is not None:
            moved = abs(ev.position().x() - self._press_x) > 4
            self._drag = None
            if not moved:
                side = self._side_at(ev.position().y())
                if side is not None:
                    t = self.from_axis(side, self._axis_at(ev.position().x()))
                    if -0.5 <= t <= self.dur[side]:
                        self.seek_requested.emit(side, max(0.0, t))

    def mouseDoubleClickEvent(self, ev):
        self.view = self._full_range()
        self.update()
