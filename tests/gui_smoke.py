"""Oberflaeche ohne Bildschirm testen: Fenster mit einer Test-DB oeffnen, Paar waehlen,
laden lassen, A/B umschalten (ohne Soundkarte) und Bildschirmfotos speichern.

python tests/gui_smoke.py <db> <ausgabeordner> [suchtext]"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtWidgets import QApplication

import gui.main_window as mw
from gui.theme import apply_dark


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main():
    db, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    mw.CONFIG_PATH = mw.Path(out) / "config.json"
    app = QApplication(sys.argv)
    apply_dark(app)
    w = mw.MainWindow(db)
    w.resize(1500, 950)
    w.show()
    pump(app, 0.5)
    if len(sys.argv) > 3:
        w.search.setText(sys.argv[3])
        pump(app, 0.6)
    print("Zeilen:", w.proxy.rowCount(), "|", w.lbl_count.text(), "|", w.lbl_status.text())
    w.table.selectRow(0)
    for _ in range(300):
        pump(app, 0.1)
        if w.player.pair_id is not None and not w._computing:
            break
    p = w.current
    print("Paar:", p["kind"], p["similarity"], "geladen:", w.player.pair_id is not None,
          "Dauer:", [round(d, 1) for d in w.player.dur])
    w.player.seek(0, 60.0)
    w.player.switch()
    print("Umschalten A 60,0 s -> B", round(w.player.position(1), 3), "s; zurueck ->", end=" ")
    w.player.switch()
    print(round(w.player.position(0), 3), "s")
    w.player.seek(0, 60.0)
    pump(app, 0.3)
    print("Anzeige:", w.view.side, [round(x, 2) for x in w.view.play_t], w.view.partner_t, "Positionstext:", w.lbl_pos.text())
    w.grab().save(os.path.join(out, "hauptfenster.png"))
    print("gespeichert:", os.path.join(out, "hauptfenster.png"))
    w.close()


if __name__ == "__main__":
    main()
