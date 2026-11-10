from PySide6.QtGui import QColor, QPalette

# Rot/Gruen-Verlauf der Wellenformen (Aehnlichkeit je Fenster)
SIM_RED = QColor(214, 64, 58)
SIM_YELLOW = QColor(222, 190, 70)
SIM_GREEN = QColor(72, 186, 104)
NO_COUNTERPART = QColor(105, 108, 116)      # Stelle gibt es in der anderen Datei nicht
PENDING_WAVE = QColor(120, 160, 190)        # Feinvergleich steht noch aus
PLAYHEAD = QColor(255, 255, 255)
ACTIVE_LANE = QColor(60, 110, 160, 70)

KIND_LABELS = {
    "identisch": "Identisch",
    "ausschnitt": "Ausschnitt",
    "variante": "Tempo/Tonhöhe",
    "teilweise": "Gemeinsame Teile",
    "version": "Andere Aufnahme",
    "schwach": "Schwach",
    "": "Noch nicht verglichen",
}
KIND_TIPS = {
    "identisch": "Beide Dateien enthalten dieselbe Aufnahme fast vollständig",
    "ausschnitt": "Eine Datei steckt (fast) vollständig in der anderen – z. B. Radio-Edit, gekürzte Fassung",
    "variante": "Dieselbe Aufnahme schneller/langsamer oder in anderer Tonhöhe",
    "teilweise": "Gemeinsame Abschnitte – z. B. Remix, Extended Mix, Medley",
    "version": "Gleicher Song, aber andere Aufnahme (live, neu eingespielt, andere Sprache)",
    "schwach": "Nur kurze oder überlagerte Gemeinsamkeiten – meist kein Duplikat",
    "": "Der Feinvergleich (Phase 3) für dieses Paar läuft noch",
}
KIND_COLORS = {
    "identisch": QColor(72, 186, 104),
    "ausschnitt": QColor(120, 200, 150),
    "variante": QColor(90, 170, 220),
    "teilweise": QColor(222, 190, 70),
    "version": QColor(200, 140, 220),
    "schwach": QColor(140, 140, 140),
    "": QColor(140, 160, 180),
}


def sim_color(s: float) -> QColor:
    """Aehnlichkeit -> Farbe: unter RED rot, ueber GREEN gruen, dazwischen ueber Gelb."""
    from engine.compare import GREEN, RED
    if s != s:  # NaN
        return NO_COUNTERPART
    if s >= GREEN:
        return SIM_GREEN
    if s <= RED:
        return SIM_RED
    x = (s - RED) / (GREEN - RED)
    a, b = (SIM_RED, SIM_YELLOW) if x < 0.5 else (SIM_YELLOW, SIM_GREEN)
    f = x * 2 if x < 0.5 else (x - 0.5) * 2
    return QColor(int(a.red() + (b.red() - a.red()) * f), int(a.green() + (b.green() - a.green()) * f),
                  int(a.blue() + (b.blue() - a.blue()) * f))


def apply_dark(app):
    app.setStyle("Fusion")
    p = QPalette()
    base = QColor(30, 32, 36)
    p.setColor(QPalette.Window, QColor(38, 40, 45))
    p.setColor(QPalette.WindowText, QColor(225, 225, 225))
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor(44, 46, 52))
    p.setColor(QPalette.ToolTipBase, QColor(50, 52, 58))
    p.setColor(QPalette.ToolTipText, QColor(235, 235, 235))
    p.setColor(QPalette.Text, QColor(225, 225, 225))
    p.setColor(QPalette.Button, QColor(52, 55, 61))
    p.setColor(QPalette.ButtonText, QColor(225, 225, 225))
    p.setColor(QPalette.Highlight, QColor(60, 110, 160))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    p.setColor(QPalette.PlaceholderText, QColor(140, 140, 140))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 120, 120))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 120, 120))
    app.setPalette(p)
    app.setStyleSheet("QToolTip { border: 1px solid #555; padding: 4px; }")
