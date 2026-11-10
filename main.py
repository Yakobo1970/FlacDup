import multiprocessing
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from gui.main_window import MainWindow
from gui.theme import apply_dark
from paths import ASSETS_DIR
from version import APP_NAME, VERSION

ICON_PATH = ASSETS_DIR / "icon.ico"


def main():
    if sys.platform == "win32":
        # Eigene App-Kennung, damit die Taskleiste das FlacDup-Icon zeigt statt des Python-Icons
        import ctypes
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"undabtanzbar.{APP_NAME}.{VERSION}")
        except Exception:
            pass
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    # Deutsche Standardtexte in Qt-Dialogen ("Ja"/"Nein" statt "Yes"/"No")
    from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
    translator = QTranslator(app)
    if translator.load(QLocale(QLocale.German), "qtbase", "_",
                       QLibraryInfo.path(QLibraryInfo.TranslationsPath)):
        app.installTranslator(translator)
    apply_dark(app)
    if ICON_PATH.exists():
        app.setWindowIcon(QIcon(str(ICON_PATH)))

    def excepthook(etype, value, tb):
        # Ohne Konsole (pythonw) wuerden Fehler sonst spurlos verschwinden
        from gui.main_window import ERROR_LOG, log_error
        from PySide6.QtWidgets import QMessageBox
        log_error("unerwarteter Fehler", value.with_traceback(tb))
        QMessageBox.critical(None, APP_NAME, f"Unerwarteter Fehler:\n{value}\n\nDetails: {ERROR_LOG}")

    sys.excepthook = excepthook
    db_path = sys.argv[sys.argv.index("--db") + 1] if "--db" in sys.argv else None
    window = MainWindow(db_path)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    # Noetig fuer die Worker-Prozesse (ProcessPoolExecutor) in einer gebauten .exe
    multiprocessing.freeze_support()
    if len(sys.argv) >= 4 and sys.argv[1] == "--selftest":
        from selftest import run
        sys.exit(run(sys.argv[2], sys.argv[3]))
    main()
