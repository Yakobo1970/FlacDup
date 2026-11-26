"""Aktionen im Hauptfenster samt Rueckgaengig an einer Test-DB pruefen (offscreen):
Kein Duplikat, Tags/Name im Editor, Schneiden + Neubewertung, Papierkorb, Tag-Abgleich -- jeweils mit Strg+Z.

ACHTUNG: schreibt in die Dateien der DB (Papierkorb, Schnitt, Tags). Nur mit einer DB aufrufen,
deren Dateien Kopien in einem Testordner sind (z. B. per tests/cli_run.py aus Kopien gebaut).

python tests/gui_actions.py <test.db> <Testordner>"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FLACDUP_UPDATE_TEST", "0")   # keine Netzabfrage im Test
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from PySide6.QtWidgets import QApplication, QMessageBox

import gui.main_window as mw
from engine import editing
from gui.pair_model import PAIR_ROLE
from gui.theme import apply_dark

FAILS = []


def check(name, ok, info=""):
    print(("OK    " if ok else "FEHLER ") + name + (f"  ({info})" if info else ""))
    if not ok:
        FAILS.append(name)


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def wait(app, cond, timeout=60):
    end = time.time() + timeout
    while time.time() < end and not cond():
        pump(app, 0.1)
    return cond()


def main():
    db, folder = sys.argv[1], os.path.normcase(os.path.abspath(sys.argv[2]))
    import sqlite3
    paths = [r[0] for r in sqlite3.connect(db).execute("SELECT path FROM files")]
    foreign = [p for p in paths if not os.path.normcase(p).startswith(folder)]
    if foreign:
        sys.exit(f"Abbruch: {len(foreign)} Pfade ausserhalb des Testordners, z. B. {foreign[0]}")
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
    QMessageBox.warning = staticmethod(lambda *a, **k: print("  Warnung:", a[2]))
    mw.CONFIG_PATH = mw.Path(folder) / "config.json"
    app = QApplication(sys.argv)
    apply_dark(app)
    w = mw.MainWindow(db)
    w.resize(1500, 950)
    w.show()
    pump(app, 0.5)

    def select_first():
        w.table.selectRow(0)
        return wait(app, lambda: w.current is not None and not w.lbl_pos.text().startswith("lädt"))

    def editor():
        from gui.editor import EditorWindow
        ed = next(x for x in app.topLevelWidgets() if isinstance(x, EditorWindow) and x.isVisible())
        wait(app, lambda: ed.wave.peaks.shape[0] > 0)
        return ed

    check("Paar gewählt", select_first())
    pid0 = w.current["id"]
    rows0 = w.proxy.rowCount()

    # 1) Kein Duplikat + Rueckgaengig
    w._set_review("not_dup")
    check("Kein Duplikat blendet aus", w.proxy.rowCount() == rows0 - 1)
    w._do_undo()
    check("Rückgängig holt Paar zurück", w.proxy.rowCount() == rows0 and (w.current or {}).get("id") == pid0)

    # 2) Nur Tags/Name im Editor
    select_first()
    a_path = w.model.files[w.current["a_id"]]["path"]
    w._open_editor(0, 10.0)
    ed = editor()
    ed.table.item(0, 3).setText("umbenannt.mp3")
    ed.table.item(0, 4).setText("Neuer Titel")
    ed._save()
    pump(app, 0.5)
    new_path = os.path.join(os.path.dirname(a_path), "umbenannt.mp3")
    check("Umbenannt auf der Platte", os.path.exists(new_path) and not os.path.exists(a_path))
    check("Paar bleibt gewählt", (w.current or {}).get("id") == pid0, str(w.current and w.current["id"]))
    check("Knöpfe aktiv nach Editor", w.btn_trash_a.isEnabled() and w.btn_play.isEnabled())
    check("Titel im Modell", w.model.files[w.current["a_id"]]["title"] == "Neuer Titel")
    w._do_undo()
    pump(app, 0.3)
    check("Rückgängig: alter Name", os.path.exists(a_path) and not os.path.exists(new_path))
    check("Rückgängig: alter Titel", editing.read_tags(a_path).get("title") != "Neuer Titel")

    # 3) Schneiden: Intro entfernen + teilen, sofortige Neubewertung
    select_first()
    b_path = w.model.files[w.current["b_id"]]["path"]
    w._open_editor(1, 20.0)
    ed = editor()
    ed.wave.sel = (0.0, 31.5)
    ed._cut()
    ed.wave.cursor = 160.0
    ed._split()
    ed._save()
    check("Neubewertung läuft, Knöpfe gesperrt", w._reeval_busy and not w.btn_trash_a.isEnabled())
    check("Neubewertung fertig", wait(app, lambda: not w._reeval_busy, 120))
    check("Neues Paar gewählt", w.current is not None, w.lbl_status.text())
    check("Knöpfe aktiv nach Neubewertung", w.btn_trash_a.isEnabled() and w.btn_play.isEnabled())
    wait(app, lambda: not w.lbl_pos.text().startswith("lädt"))
    names = sorted(os.listdir(os.path.dirname(b_path)))
    check("Teile auf der Platte, Original im Papierkorb", not os.path.exists(b_path)
          and sum("(Teil" in n for n in names) == 2, ", ".join(names))
    print("  Paare jetzt:", [(r["source"], round(r["similarity"] or 0)) for r in w.model.rows])

    # 4) Papierkorb + Rueckgaengig
    trashed = w.model.files[w.current["a_id"]]["path"]
    n_before = w.model.rowCount()
    w._trash(0)
    check("Papierkorb entfernt Datei", not os.path.exists(trashed) and w.model.rowCount() < n_before)
    w._do_undo()
    check("Rückgängig: Datei zurück", os.path.exists(trashed) and w.model.rowCount() == n_before)

    # 5) Schnitt rueckgaengig
    w._do_undo()
    pump(app, 0.5)
    names = sorted(os.listdir(os.path.dirname(b_path)))
    check("Rückgängig: Original zurück, Teile weg", os.path.exists(b_path)
          and not any("(Teil" in n for n in names), ", ".join(names))
    check("Rückgängig: altes Paar zurück", any(r["id"] == pid0 for r in w.model.rows)
          and (w.current or {}).get("id") == pid0)

    # 6) Tag-Abgleich per Rechtsklick in die Liste + Rueckgaengig
    from PySide6.QtWidgets import QDialog
    from engine import tagsync
    from gui.pair_model import mismatch
    from gui.tag_dialog import TagCompareDialog
    select_first()
    fa, fb = w.model.files[w.current["a_id"]], w.model.files[w.current["b_id"]]
    check("Welle: Namen rot genau bei Abweichung", w.view.name_alert == any(mismatch(fa, fb)))
    pa, pb = fa["path"], fb["path"]
    before = [tagsync.read(pa), tagsync.read(pb)]

    def fake_exec(dlg):
        dlg._set_text(dlg.fields[0]["title"], "Abgleich-Titel")
        dlg._copy("title", 0)
        dlg._set_text(dlg.fields[0]["composer"], "Martin L. Gore")
        dlg._set_text(dlg.fields[1]["lyrics"], "Zeile eins\nZeile zwei")
        dlg._copy("cover", 0)
        dlg.stems[1].setText("abgleich-b")
        dlg._update()
        return QDialog.Accepted
    TagCompareDialog.exec = fake_exec
    w._tag_compare_at(w.table.visualRect(w.proxy.index(0, 0)).center())
    pump(app, 0.3)
    new_b = os.path.join(os.path.dirname(pb), "abgleich-b" + os.path.splitext(pb)[1])
    ra, rb = tagsync.read(pa), tagsync.read(new_b) if os.path.exists(new_b) else None
    check("Tag-Abgleich: B umbenannt", os.path.exists(new_b) and not os.path.exists(pb))
    check("Tag-Abgleich: Titel A und B", ra["values"]["title"] == "Abgleich-Titel"
          and rb is not None and rb["values"]["title"] == "Abgleich-Titel")
    check("Tag-Abgleich: Komponist A, Liedtext B", ra["values"]["composer"] == "Martin L. Gore"
          and rb["values"]["lyrics"] == "Zeile eins\nZeile zwei")
    check("Tag-Abgleich: Cover von A in B", tagsync.same_cover(rb["cover"], before[0]["cover"]))
    check("Tag-Abgleich: Paar bleibt, Modell aktuell", (w.current or {}).get("id") is not None
          and w.model.files[w.current["b_id"]]["path"] == new_b
          and w.model.files[w.current["a_id"]]["title"] == "Abgleich-Titel")
    w._do_undo()
    pump(app, 0.3)
    after = [tagsync.read(pa), tagsync.read(pb) if os.path.exists(pb) else None]
    check("Rückgängig: B wieder alter Name", os.path.exists(pb) and not os.path.exists(new_b))
    check("Rückgängig: Tags + Cover wie vorher", after[1] is not None and all(
        after[i]["values"] == before[i]["values"] and tagsync.same_cover(after[i]["cover"], before[i]["cover"])
        for i in (0, 1)))
    check("Rückgängig: Modell wieder alter Pfad", any(f["path"] == pb for f in w.model.files.values()))
    w.close()
    print("ALLES OK" if not FAILS else f"{len(FAILS)} FEHLER: {FAILS}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
