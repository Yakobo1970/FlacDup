import sys
from pathlib import Path


def app_base_dir() -> Path:
    """Basisverzeichnis der Anwendung: Projektwurzel im Quellcode-Betrieb,
    PyInstaller-Bundle-Wurzel (sys._MEIPASS) in einer gebauten .exe -- damit
    Pfade zu assets/ (Icon) in beiden Faellen
    gleich aufgeloest werden."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


ASSETS_DIR = app_base_dir() / "assets"
