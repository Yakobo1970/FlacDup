"""Erzeugt assets/icon.ico und assets/logo.png (Motiv: zwei uebereinanderliegende CDs –
die Dublette – mit den beiden Wellen A und B wie in der Vergleichsansicht: gleiche
Stellen gruen, eine abweichende Stelle rot; Abzeichen mit Gleichheitszeichen).
Jede Groesse wird einzeln gezeichnet; unter 48 px entfallen Rillen und Details.

Aufruf: python scripts/make_icon.py
"""
import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parents[1]
BG = QColor("#1e2024")
DISC = QColor("#3a3f47")
DISC_BACK = QColor("#2c3036")
RING = QColor("#4a505a")
GREEN = QColor("#48ba68")
RED = QColor("#d6403a")
SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]


def _disc(p, c, r, s, color, small):
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawEllipse(c, r, r)
    if not small:
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(RING, max(1.0, 2 * s)))
        p.drawEllipse(c, r * 0.84, r * 0.84)
        p.drawEllipse(c, r * 0.68, r * 0.68)
        p.setPen(Qt.NoPen)
    p.setBrush(BG)
    p.drawEllipse(c, r * 0.22, r * 0.22)


def _wave(p, s, y, pts, red_from, red_to, width):
    """Zackenlinie; der Abschnitt zwischen red_from und red_to (x) wird rot gezeichnet."""
    def seg(points, color):
        if len(points) < 2:
            return
        path = QPainterPath(QPointF(points[0][0] * s, (y + points[0][1]) * s))
        for x, dy in points[1:]:
            path.lineTo(x * s, (y + dy) * s)
        p.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPath(path)
    before = [q for q in pts if q[0] <= red_from]
    mid = [q for q in pts if red_from <= q[0] <= red_to]
    after = [q for q in pts if q[0] >= red_to]
    seg(before, GREEN)
    seg(after, GREEN)
    seg(mid, RED)


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 220.0          # Entwurf ist auf 220 x 220 angelegt
    small = size < 48

    p.setPen(Qt.NoPen)
    p.setBrush(BG)
    p.drawRoundedRect(QRectF(0, 0, size, size), 44 * s, 44 * s)

    # hintere und vordere CD (die Dublette)
    _disc(p, QPointF(132 * s, 92 * s), 74 * s, s, DISC_BACK, small)
    _disc(p, QPointF(96 * s, 124 * s), 80 * s, s, DISC, small)

    # Wellen A (oben, komplett gruen) und B (unten, mit roter Abweichung)
    if small:
        a = [(22, 0), (46, 0), (58, -18), (72, 18), (86, -14), (100, 14), (114, 0), (198, 0)]
        b = [(22, 0), (46, 0), (58, -18), (72, 18), (86, 0), (110, 0), (122, -18), (136, 18),
             (150, 0), (198, 0)]
        _wave(p, s, 92, a, 999, 999, 10 * s)
        _wave(p, s, 156, b, 108, 154, 10 * s)
    else:
        a = [(22, 0), (40, 0), (48, -16), (56, 16), (64, -22), (72, 22), (80, -12), (88, 12),
             (96, 0), (112, 0), (120, -18), (128, 18), (136, -10), (144, 10), (152, 0), (198, 0)]
        b = [(22, 0), (40, 0), (48, -16), (56, 16), (64, -22), (72, 22), (80, -12), (88, 12),
             (96, 0), (104, 0), (110, -26), (118, 26), (126, -26), (134, 26), (142, 0), (152, 0),
             (198, 0)]
        _wave(p, s, 92, a, 999, 999, 7 * s)
        _wave(p, s, 156, b, 100, 146, 7 * s)

    # Abzeichen mit Gleichheitszeichen
    r = (46 if size <= 24 else 36 if small else 28) * s
    bc = QPointF(180 * s, 40 * s) if not small else QPointF(170 * s, 50 * s)
    p.setPen(Qt.NoPen)
    p.setBrush(BG)
    p.drawEllipse(bc, r + 5 * s, r + 5 * s)
    p.setBrush(GREEN)
    p.drawEllipse(bc, r, r)
    p.setPen(QPen(BG, 0.24 * r, Qt.SolidLine, Qt.RoundCap))
    for dy in (-0.22, 0.22):
        p.drawLine(QPointF(bc.x() - 0.45 * r, bc.y() + dy * r), QPointF(bc.x() + 0.45 * r, bc.y() + dy * r))
    p.end()
    return img


def to_pil(img: QImage) -> Image.Image:
    buf = QBuffer()
    buf.open(QBuffer.ReadWrite)
    img.save(buf, "PNG")
    return Image.open(io.BytesIO(bytes(buf.data()))).convert("RGBA")


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 -- noetig fuer QPainter
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    images = [to_pil(render(sz)) for sz in SIZES]
    big = images[-1]
    big.save(assets / "icon.ico", format="ICO", sizes=[(sz, sz) for sz in SIZES],
             append_images=images[:-1])
    to_pil(render(512)).save(assets / "logo.png")
    # Kontrollbild: alle Groessen nebeneinander
    sheet = Image.new("RGBA", (sum(SIZES) + 10 * len(SIZES) + 10, 266), (240, 240, 240, 255))
    x = 10
    for sz, im in zip(SIZES, images):
        sheet.paste(im, (x, 256 - sz + 5), im)
        x += sz + 10
    sheet.save(assets / "icon_preview.png")
    print("geschrieben:", assets / "icon.ico", Image.open(assets / "icon.ico").info.get("sizes"))


if __name__ == "__main__":
    main()
