"""Selbsttest fuer die gebaute .exe (die Fenster-Version hat keine Konsole).

Aufruf: FlacDup.exe --selftest <Audiodatei> <Ausgabe.txt>
Prueft die gebuendelten Bibliotheken, Fingerabdruck und Feinvergleich in einem Worker-Prozess
(wie bei der Analyse), Tags lesen, die deutsche Qt-Uebersetzung und das Hauptfenster mit eigener
Test-Datenbank. Schreibt alles in die Ausgabedatei, Rueckgabe 0 = ok.
"""
from __future__ import annotations

import sys
import traceback
from concurrent.futures import ProcessPoolExecutor


def run(audio: str, out: str) -> int:
    lines, ok = [], True

    def step(name, fn):
        nonlocal ok
        try:
            lines.append(f"OK   {name}: {fn()}")
        except Exception:
            ok = False
            lines.append(f"FEHLER {name}:\n{traceback.format_exc()}")

    from version import APP_NAME, VERSION
    lines.append(f"{APP_NAME} {VERSION} – Selbsttest, frozen={getattr(sys, 'frozen', False)}")
    step("numpy/scipy", lambda: __import__("numpy").__version__ + " / " + __import__("scipy").__version__)
    step("soundfile", lambda: __import__("soundfile").__libsndfile_version__)
    step("PyAV/FFmpeg", lambda: __import__("av").__version__)
    step("sounddevice/PortAudio", lambda: __import__("sounddevice").get_portaudio_version()[1])
    step("mutagen", lambda: __import__("mutagen").version_string)

    def probe():
        from engine.decode import probe as p
        i = p(audio)
        return f"{i.codec}, {i.sr} Hz, {i.channels} Kanäle, {i.frames / i.sr:.1f} s"
    step("Datei lesen", probe)

    def fingerprint():
        from engine.fingerprint import fingerprint_file
        with ProcessPoolExecutor(max_workers=1) as ex:   # wie Phase 1: eigener Worker-Prozess
            res = ex.submit(fingerprint_file, audio).result(timeout=600)
        if not res.get("ok"):
            raise RuntimeError(res.get("error"))
        return f"{res['n_peaks']} Spektralspitzen, {res['duration']:.1f} s"
    step("Fingerabdruck im Worker-Prozess", fingerprint)

    def compare():
        from engine.compare import compare_pair
        with ProcessPoolExecutor(max_workers=1) as ex:   # wie Phase 3
            res = ex.submit(compare_pair, audio, audio, 1.0, 0.0, 0.0).result(timeout=600)
        if res["similarity"] < 90:
            raise RuntimeError(f"Datei mit sich selbst nur {res['similarity']:.0f} % gleich")
        return f"mit sich selbst {res['similarity']:.0f} % gleich, Median {res['median']:.2f}"
    step("Feinvergleich im Worker-Prozess", compare)

    def tags():
        from engine.tags import read_tags
        t = read_tags(audio)
        return f"Band '{t.get('artist') or ''}', Titel '{t.get('title') or ''}'"
    step("Tags lesen", tags)

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])  # noqa: F841

    def translation():
        from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
        tr = QTranslator()
        if not tr.load(QLocale(QLocale.German), "qtbase", "_", QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
            raise RuntimeError("qtbase_de nicht gefunden")
        return tr.translate("QPlatformTheme", "Cancel") or "geladen"
    step("Deutsche Qt-Texte", translation)

    def assets():
        from paths import ASSETS_DIR
        missing = [n for n in ("icon.ico", "editor_icon.ico", "logo.png") if not (ASSETS_DIR / n).exists()]
        if missing:
            raise RuntimeError(f"fehlt: {missing}")
        return "Icons vorhanden"
    step("Assets", assets)

    def window():
        # eigene Test-Datenbank neben der Ausgabedatei, die echte Konfiguration bleibt unberuehrt
        import os
        from pathlib import Path
        import gui.main_window as mw
        from gui.theme import apply_dark
        work = Path(out).with_suffix("")
        work.mkdir(parents=True, exist_ok=True)
        mw.CONFIG_PATH = work / "config.json"
        apply_dark(app)
        w = mw.MainWindow(str(work / "selftest.db"))
        w.resize(1300, 800)
        w.show()
        app.processEvents()
        shot = str(work / "hauptfenster.png")
        w.grab().save(shot)
        w.close()
        return f"aufgebaut, Bild {os.path.basename(shot)}"
    step("Hauptfenster", window)

    lines.append("ALLES OK" if ok else "FEHLER GEFUNDEN")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0 if ok else 1
