from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget, QTextBrowser,
                               QVBoxLayout)

from paths import ASSETS_DIR
from version import APP_NAME, VERSION

WEBSITE_URL = "https://www.undabtanzbar.de"
DONATE_URL = "https://paypal.me/yakobo"

ABOUT_HTML = f"""
<h3>Was macht dieses Programm?</h3>
<p>{APP_NAME} findet doppelte Musik in ganzen Musikordnern samt Unterordnern (FLAC, MP3, WAV,
AIFF, OGG, M4A, WMA, APE …) – und zwar am <i>Klang</i>, nicht an Dateinamen oder Tags. Erkannt
werden auch Ausschnitte (Radio-Edit, gekürzte Fassung), Remixe und Extended-Versionen mit
gemeinsamen Teilen, schneller oder langsamer abgespielte und in der Tonhöhe verschobene
Fassungen sowie andere Aufnahmen desselben Songs.</p>

<h3>Kurzanleitung</h3>
<ol>
<li>„Ordner hinzufügen …“ oder Ordner ins Fenster ziehen. Ordner addieren sich; bereits
analysierte, unveränderte Dateien werden übersprungen.</li>
<li>Die Analyse läuft in drei Phasen. Jedes Ergebnis wird sofort gespeichert – nach einem
Abbruch macht „Analyse fortsetzen“ an derselben Stelle weiter. Funde aus Phase 2 und 3
erscheinen schon während der Analyse in der Liste und lassen sich sofort anhören.</li>
<li>Paar in der Liste wählen: Beide Wellen stehen untereinander, gemeinsame Stellen
übereinander. <span style="color:#48ba68"><b>Grün</b></span> klingt gleich,
<span style="color:#d6403a"><b>rot</b></span> verschieden, grau gibt es in der anderen Datei
nicht.</li>
<li>Abspielen und mit <b>Tab</b> (oder A/B) umschalten – an der entsprechenden Stelle der
anderen Datei, sample-genau abgeglichen. Es klingt wie ein durchgehender Track.</li>
<li>Überflüssige Datei in den Papierkorb legen, oder das Paar als „Kein Duplikat“ bzw.
„Erledigt“ markieren.</li>
</ol>

<h3>Die drei Phasen</h3>
<p><b>1 Index</b> – je Datei ein Klang-Fingerabdruck aus markanten Spektralspitzen.<br>
<b>2 Abgleich</b> – sucht alle Dateien, die ein Stück Musik gemeinsam haben, mit beliebigem
Zeitversatz; auf Wunsch auch Tempo/Tonhöhe ±10&nbsp;%.<br>
<b>3 Feinvergleich</b> – vergleicht jedes Paar Sekunde für Sekunde, liefert Rot/Grün,
Übereinstimmung und Art; auf Wunsch zusätzlich Dateien gleicher Band mit gleichem Titel.</p>

<h3>Wichtiger Hinweis</h3>
<p>Die Erkennung ist ein <b>Hilfsmittel</b>, kein Ersatz fürs eigene Hinhören. Ob eine
Remaster-Fassung, ein Edit oder eine Live-Aufnahme behalten wird, entscheidet der Mensch –
{APP_NAME} löscht nichts von allein und legt Dateien nur in den Papierkorb.</p>

<h3>Links</h3>
<p>Website: <a href="{WEBSITE_URL}">www.undabtanzbar.de</a></p>

<h3>Entwicklung</h3>
<p>Idee, Konzept und Regie: <b>Kay 'Yakobo' Fiedler</b>.<br>
Umgesetzt mit Claude (Anthropic) als KI-Programmierassistent für Code, Signalanalyse und
Kalibrierung – abgestimmt an echten Dubletten aus dem eigenen Musikarchiv.</p>

<h3>Unterstützen</h3>
<p>{APP_NAME} ist kostenlos. Wer die Entwicklung unterstützen möchte, kann über den
Knopf unten eine kleine Spende per PayPal schicken – vielen Dank!</p>
"""


TIPS_HTML = """
<h3>Die Arten in der Liste</h3>
<table cellspacing="0" cellpadding="3" border="1" style="border-collapse:collapse">
<tr><td><b style="color:#48ba68">Identisch</b></td><td>dieselbe Aufnahme, fast vollständig in beiden Dateien</td></tr>
<tr><td><b style="color:#78c896">Ausschnitt</b></td><td>eine Datei steckt (fast) komplett in der anderen – Radio-Edit, gekürzte Fassung</td></tr>
<tr><td><b style="color:#5aaadc">Tempo/Tonhöhe</b></td><td>dieselbe Aufnahme schneller/langsamer oder in anderer Tonhöhe</td></tr>
<tr><td><b style="color:#debe46">Gemeinsame Teile</b></td><td>Remix, Extended Mix, Medley</td></tr>
<tr><td><b style="color:#c88cdc">Andere Aufnahme</b></td><td>gleicher Song, aber live, neu eingespielt oder in anderer Sprache</td></tr>
<tr><td><b style="color:#8c8c8c">Schwach</b></td><td>nur kurze oder überlagerte Gemeinsamkeiten – meist kein Duplikat (standardmäßig ausgeblendet)</td></tr>
</table>

<h3>Schnellster Weg zum Aufräumen</h3>
<ul>
<li>Filter „Art“ zuerst auf <b>Identisch</b> stellen – das sind die eindeutigen Dubletten.
Spalte <b>Ordner</b> sortieren, dann stehen Dubletten aus denselben Alben zusammen.</li>
<li>Danach <b>Ausschnitt</b> und <b>Tempo/Tonhöhe</b>: hier lohnt das A/B-Hören, welche Fassung
bleiben soll.</li>
<li><b>Gemeinsame Teile</b> und <b>Andere Aufnahme</b> sind meist gewollte Versionen –
mit <b>N</b> als „Kein Duplikat“ markieren, dann tauchen sie nie wieder auf.</li>
</ul>

<h3>Woran man die Übereinstimmung abliest</h3>
<p>Gemessen an echten Dateien des eigenen Archivs (Übereinstimmung je Sekunde, 1 = gleich):</p>
<table cellspacing="0" cellpadding="3" border="1" style="border-collapse:collapse">
<tr><th align="left">Paar</th><th align="right">typischer Wert</th></tr>
<tr><td>dieselbe Aufnahme (auch andere Kodierung/Bitrate)</td><td align="right">0,90 – 0,98</td></tr>
<tr><td>andere Aufnahme desselben Songs</td><td align="right">0,70 – 0,85</td></tr>
<tr><td>verschiedene Songs</td><td align="right">unter 0,45</td></tr>
</table>
<p>Ab 0,85 wird eine Stelle grün. Die Spalte „Gleich“ zeigt, welcher Anteil der Datei gleich
klingend in der anderen vorkommt – beim Ausschnitt der Anteil der kürzeren Datei.
Mit der Maus über einer Welle steht der genaue Wert der Stelle.</p>

<h3>Tastatur</h3>
<ul>
<li><b>Leertaste</b> Abspielen/Pause · <b>Tab</b> A/B umschalten · <b>A</b> / <b>B</b> gezielt wählen</li>
<li><b>←</b> / <b>→</b> 5 Sekunden zurück/vor</li>
<li><b>N</b> Kein Duplikat · <b>E</b> Erledigt</li>
<li>Mausrad in der Welle = Zoom, Ziehen = verschieben, Doppelklick = alles zeigen</li>
<li><b>Rechtsklick</b> in eine Welle öffnet den Editor für diese Datei</li>
</ul>

<h3>Weitere Tipps</h3>
<ul>
<li>„Tempo/Tonhöhe“ verlängert den Abgleich etwa um das Zwanzigfache. Wer zuerst nur die
klaren Dubletten sucht, schaltet es für den ersten Lauf aus und später wieder ein – dann
werden nur die Varianten nachgesucht.</li>
<li>„Gleiche Titel“ findet auch sehr alte oder stark bearbeitete Aufnahmen, bei denen der
Klang-Fingerabdruck nicht greift.</li>
<li>Verschobene Dateien werden an Name und Spieldauer wiedererkannt und behalten ihren
Fingerabdruck und ihren Prüfstand.</li>
<li>Der <b>Editor</b> (Rechtsklick in eine Welle) schneidet verlustfrei: Anfang/Ende kürzen, Bereiche
entfernen, in mehrere Teile zerlegen, Tags und Dateinamen ändern. Rot markiert sind oben die Stellen, die in der
anderen Datei anders klingen. Die neuen Dateien werden als ungeprüft eingetragen und beim nächsten
„Analyse fortsetzen“ neu analysiert; das Original wandert als »(Original) Name« in den Papierkorb.</li>
<li>Mehrere Archive lassen sich in getrennten Datenbanken führen (Menü Datei).</li>
</ul>
"""


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Über {APP_NAME} {VERSION}")
        self.resize(640, 640)
        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        logo = ASSETS_DIR / "logo.png"
        if logo.exists():
            icon_label = QLabel(self)
            icon_label.setPixmap(QPixmap(str(logo)).scaled(72, 72, Qt.KeepAspectRatio,
                                                           Qt.SmoothTransformation))
            header.addWidget(icon_label)
        title_box = QVBoxLayout()
        title_box.addWidget(QLabel(f"<b style='font-size:17pt'>{APP_NAME}</b>", self))
        title_box.addWidget(QLabel(f"Version {VERSION} · Audio-Dublettenfinder", self))
        header.addSpacing(8)
        header.addLayout(title_box)
        header.addStretch(1)
        layout.addLayout(header)

        tabs = QTabWidget(self)
        for label, html in ((f"Über {APP_NAME}", ABOUT_HTML), ("Tooltipps", TIPS_HTML)):
            browser = QTextBrowser(self)
            browser.setOpenExternalLinks(True)
            browser.setHtml(html)
            tabs.addTab(browser, label)
        self.tabs = tabs
        layout.addWidget(tabs, stretch=1)

        buttons = QHBoxLayout()
        donate = QPushButton("♥  Spenden via PayPal", self)
        donate.setToolTip(DONATE_URL)
        donate.setStyleSheet("QPushButton { padding: 6px 14px; font-weight: bold; }")
        donate.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DONATE_URL)))
        web = QPushButton("www.undabtanzbar.de", self)
        web.setToolTip(WEBSITE_URL)
        web.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(WEBSITE_URL)))
        close_btn = QPushButton("Schließen", self)
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(donate)
        buttons.addWidget(web)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
