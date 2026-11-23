from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPushButton, QTabWidget, QTextBrowser,
                               QVBoxLayout)

from lang import T, by_lang
from paths import ASSETS_DIR
from version import APP_NAME, VERSION

WEBSITE_URL = "https://www.undabtanzbar.de"
DONATE_URL = "https://paypal.me/yakobo"
GREEN, BLUE, PINK, RED = "#48ba68", "#5cb6ec", "#e86ebe", "#d6403a"

ABOUT_DE = f"""
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
übereinander. <span style="color:{RED}"><b>Rot</b></span> klingt verschieden, grau gibt es in der
anderen Datei nicht. „Klingt gleich“ ist je Format eingefärbt: MP3
<span style="color:{GREEN}"><b>grün</b></span>, FLAC <span style="color:{BLUE}"><b>himmelblau</b></span>,
alle anderen Formate <span style="color:{PINK}"><b>pink</b></span>.</li>
<li>Abspielen und mit <b>Tab</b> (oder A/B) umschalten – an der entsprechenden Stelle der
anderen Datei, sample-genau abgeglichen. Es klingt wie ein durchgehender Track.</li>
<li>Überflüssige Datei in den Papierkorb legen oder das Paar als „Kein Duplikat“ markieren.
<b>Strg+Z</b> macht beides rückgängig, ebenso Schnitte und Umbenennungen im Editor.</li>
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

ABOUT_EN = f"""
<h3>What does this program do?</h3>
<p>{APP_NAME} finds duplicate music in entire music folders including subfolders (FLAC, MP3, WAV,
AIFF, OGG, M4A, WMA, APE …) – by <i>sound</i>, not by file names or tags. It also recognises
excerpts (radio edit, shortened version), remixes and extended versions with shared parts,
versions played faster or slower or shifted in pitch, and other recordings of the same song.</p>

<h3>Quick start</h3>
<ol>
<li>“Add folder …” or drag folders into the window. Folders add up; files that were already
analysed and have not changed are skipped.</li>
<li>The analysis runs in three phases. Every result is saved immediately – after an interruption,
“Continue analysis” picks up where it left off. Matches from phases 2 and 3 appear in the list
while the analysis is still running and can be played right away.</li>
<li>Select a pair in the list: both waveforms are shown one above the other, shared passages aligned.
<span style="color:{RED}"><b>Red</b></span> sounds different, grey does not exist in the other file.
“Sounds the same” is coloured by format: MP3 <span style="color:{GREEN}"><b>green</b></span>,
FLAC <span style="color:{BLUE}"><b>sky blue</b></span>, all other formats
<span style="color:{PINK}"><b>pink</b></span>.</li>
<li>Play and switch with <b>Tab</b> (or A/B) – to the matching position in the other file, aligned
to the sample. It sounds like one continuous track.</li>
<li>Move the redundant file to the recycle bin or mark the pair as “Not a duplicate”.
<b>Ctrl+Z</b> undoes both, as well as cuts and renames in the editor.</li>
</ol>

<h3>The three phases</h3>
<p><b>1 Index</b> – one sound fingerprint per file, made of distinctive spectral peaks.<br>
<b>2 Matching</b> – finds all files that share a piece of music, at any time offset; optionally
also tempo/pitch ±10&nbsp;%.<br>
<b>3 Fine comparison</b> – compares every pair second by second and yields red/green, similarity
and type; optionally also files of the same band with the same title.</p>

<h3>Important</h3>
<p>The detection is an <b>aid</b>, not a replacement for your own ears. Whether a remastered
version, an edit or a live recording stays is up to you – {APP_NAME} never deletes anything on its
own and only moves files to the recycle bin.</p>

<h3>Links</h3>
<p>Website: <a href="{WEBSITE_URL}">www.undabtanzbar.de</a></p>

<h3>Development</h3>
<p>Idea, concept and direction: <b>Kay 'Yakobo' Fiedler</b>.<br>
Built with Claude (Anthropic) as AI programming assistant for code, signal analysis and
calibration – tuned on real duplicates from my own music archive.</p>

<h3>Support</h3>
<p>{APP_NAME} is free. If you would like to support its development, you can send a small
donation via PayPal with the button below – thank you!</p>
"""

ABOUT_FR = f"""
<h3>Que fait ce programme ?</h3>
<p>{APP_NAME} trouve la musique en double dans des dossiers entiers, sous-dossiers compris (FLAC,
MP3, WAV, AIFF, OGG, M4A, WMA, APE …) – d’après le <i>son</i>, pas d’après les noms de fichiers ou
les tags. Il reconnaît aussi les extraits (radio edit, version raccourcie), les remixes et versions
longues ayant des parties communes, les versions jouées plus vite ou plus lentement ou transposées,
ainsi que d’autres enregistrements du même morceau.</p>

<h3>Prise en main</h3>
<ol>
<li>« Ajouter un dossier … » ou glisser des dossiers dans la fenêtre. Les dossiers s’additionnent ;
les fichiers déjà analysés et inchangés sont ignorés.</li>
<li>L’analyse se déroule en trois phases. Chaque résultat est enregistré aussitôt – après une
interruption, « Poursuivre l’analyse » reprend au même endroit. Les résultats des phases 2 et 3
apparaissent dans la liste pendant l’analyse et peuvent être écoutés tout de suite.</li>
<li>Choisir une paire dans la liste : les deux formes d’onde sont superposées, les passages communs
alignés. <span style="color:{RED}"><b>Rouge</b></span> sonne différemment, gris n’existe pas dans
l’autre fichier. « Sonne pareil » est coloré selon le format : MP3
<span style="color:{GREEN}"><b>vert</b></span>, FLAC <span style="color:{BLUE}"><b>bleu ciel</b></span>,
tous les autres formats <span style="color:{PINK}"><b>rose</b></span>.</li>
<li>Lire et basculer avec <b>Tab</b> (ou A/B) – au même endroit de l’autre fichier, à
l’échantillon près. On dirait un seul morceau continu.</li>
<li>Mettre le fichier superflu à la corbeille ou marquer la paire comme « Pas un doublon ».
<b>Ctrl+Z</b> annule les deux, ainsi que les coupes et renommages dans l’éditeur.</li>
</ol>

<h3>Les trois phases</h3>
<p><b>1 Index</b> – une empreinte sonore par fichier, faite de pics spectraux marquants.<br>
<b>2 Rapprochement</b> – cherche tous les fichiers qui partagent un passage musical, avec un
décalage quelconque ; en option aussi tempo/hauteur ±10&nbsp;%.<br>
<b>3 Comparaison fine</b> – compare chaque paire seconde par seconde et fournit rouge/vert,
concordance et type ; en option aussi les fichiers du même groupe portant le même titre.</p>

<h3>Remarque importante</h3>
<p>La détection est une <b>aide</b>, elle ne remplace pas l’écoute. Garder une version remasterisée,
un edit ou un enregistrement live reste votre décision – {APP_NAME} ne supprime rien de lui-même
et ne fait que déplacer des fichiers vers la corbeille.</p>

<h3>Liens</h3>
<p>Site web : <a href="{WEBSITE_URL}">www.undabtanzbar.de</a></p>

<h3>Développement</h3>
<p>Idée, conception et direction : <b>Kay 'Yakobo' Fiedler</b>.<br>
Réalisé avec Claude (Anthropic) comme assistant de programmation IA pour le code, l’analyse du
signal et le calibrage – ajusté sur de vrais doublons de ma propre collection.</p>

<h3>Soutenir</h3>
<p>{APP_NAME} est gratuit. Pour soutenir son développement, vous pouvez envoyer un petit don
via PayPal avec le bouton ci-dessous – merci !</p>
"""

_TABLE = '<table cellspacing="0" cellpadding="3" border="1" style="border-collapse:collapse">'

TIPS_DE = f"""
<h3>Die Arten in der Liste</h3>
{_TABLE}
<tr><td><b style="color:#48ba68">Identisch</b></td><td>dieselbe Aufnahme, fast vollständig in beiden Dateien</td></tr>
<tr><td><b style="color:#78c896">Ausschnitt</b></td><td>eine Datei steckt (fast) komplett in der anderen – Radio-Edit, gekürzte Fassung</td></tr>
<tr><td><b style="color:#5aaadc">Tempo/Tonhöhe</b></td><td>dieselbe Aufnahme schneller/langsamer oder in anderer Tonhöhe</td></tr>
<tr><td><b style="color:#debe46">Gemeinsame Teile</b></td><td>Remix, Extended Mix, Medley</td></tr>
<tr><td><b style="color:#c88cdc">Andere Aufnahme</b></td><td>gleicher Song, aber live, neu eingespielt oder in anderer Sprache</td></tr>
<tr><td><b style="color:#8c8c8c">Schwach</b></td><td>nur kurze oder überlagerte Gemeinsamkeiten – meist kein Duplikat (standardmäßig ausgeblendet)</td></tr>
</table>
<p><b style="color:#ff5c50">Rot geschrieben</b> sind Band bzw. Titel, wenn sich
der Interpret oder der Haupttitel (ohne Klammerzusätze wie „(xyz-Mix)“) der beiden Dateien
unterscheidet – dort lohnt ein genauer Blick.</p>

<h3>Schnellster Weg zum Aufräumen</h3>
<ul>
<li>Filter „Art“ zuerst auf <b>Identisch</b> stellen – das sind die eindeutigen Dubletten.
Spalte <b>Ordner</b> sortieren, dann stehen Dubletten aus denselben Alben zusammen.</li>
<li>Danach <b>Ausschnitt</b> und <b>Tempo/Tonhöhe</b>: hier lohnt das A/B-Hören, welche Fassung
bleiben soll.</li>
<li><b>Gemeinsame Teile</b> und <b>Andere Aufnahme</b> sind meist gewollte Versionen –
mit <b>N</b> als „Kein Duplikat“ markieren, dann tauchen sie nicht wieder auf. Unter
„Status: Kein Duplikat“ holt „Wieder in die Liste“ ein Paar zurück.</li>
</ul>

<h3>Woran man die Übereinstimmung abliest</h3>
<p>Gemessen an echten Dateien des eigenen Archivs (Übereinstimmung je Sekunde, 1 = gleich):</p>
{_TABLE}
<tr><th align="left">Paar</th><th align="right">typischer Wert</th></tr>
<tr><td>dieselbe Aufnahme (auch andere Kodierung/Bitrate)</td><td align="right">0,90 – 0,98</td></tr>
<tr><td>andere Aufnahme desselben Songs</td><td align="right">0,70 – 0,85</td></tr>
<tr><td>verschiedene Songs</td><td align="right">unter 0,45</td></tr>
</table>
<p>Ab 0,85 gilt eine Stelle als gleich (MP3 grün, FLAC himmelblau, andere Formate pink). Die Spalte
„Gleich“ zeigt, welcher Anteil der Datei gleich klingend in der anderen vorkommt – beim Ausschnitt
der Anteil der kürzeren Datei. Mit der Maus über einer Welle steht der genaue Wert der Stelle.</p>

<h3>Tastatur</h3>
<ul>
<li><b>Leertaste</b> Abspielen/Pause · <b>Tab</b> A/B umschalten · <b>A</b> / <b>B</b> gezielt wählen</li>
<li><b>←</b> / <b>→</b> 5 Sekunden zurück/vor</li>
<li><b>N</b> Kein Duplikat · <b>Strg+Z</b> Rückgängig</li>
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
entfernen, in mehrere Teile zerlegen, Tags und Dateinamen ändern. Rot markiert sind oben die Stellen,
die in der anderen Datei anders klingen. Nach dem Speichern werden die neuen Dateien sofort mit dem
Partner verglichen und erscheinen als neues Paar; der Abgleich mit dem übrigen Archiv folgt beim nächsten
„Analyse fortsetzen“. Das Original wandert als »(Original) Name« in den Papierkorb – Strg+Z holt es zurück.</li>
<li>Mehrere Archive lassen sich in getrennten Datenbanken führen (Menü Datei).</li>
<li>Die Sprache stellt man unter Hilfe → „Sprache · Language · Langue“ um; FlacDup sieht einmal am Tag
nach, ob es eine neue Version gibt (abschaltbar im Menü Hilfe).</li>
</ul>
"""

TIPS_EN = f"""
<h3>The types in the list</h3>
{_TABLE}
<tr><td><b style="color:#48ba68">Identical</b></td><td>the same recording, almost completely in both files</td></tr>
<tr><td><b style="color:#78c896">Excerpt</b></td><td>one file is (almost) completely contained in the other – radio edit, shortened version</td></tr>
<tr><td><b style="color:#5aaadc">Tempo/pitch</b></td><td>the same recording faster/slower or in a different pitch</td></tr>
<tr><td><b style="color:#debe46">Shared parts</b></td><td>remix, extended mix, medley</td></tr>
<tr><td><b style="color:#c88cdc">Other recording</b></td><td>same song, but live, re-recorded or in another language</td></tr>
<tr><td><b style="color:#8c8c8c">Weak</b></td><td>only short or overlaid similarities – usually not a duplicate (hidden by default)</td></tr>
</table>
<p><b style="color:#ff5c50">Red text</b> marks band or title when the
artist or the main title (without bracketed additions like “(xyz mix)”) of the two files differ –
worth a closer look.</p>

<h3>Fastest way to clean up</h3>
<ul>
<li>Set the “Type” filter to <b>Identical</b> first – these are the clear duplicates. Sort by the
<b>Folder</b> column so duplicates from the same albums stand together.</li>
<li>Then <b>Excerpt</b> and <b>Tempo/pitch</b>: A/B listening helps to decide which version stays.</li>
<li><b>Shared parts</b> and <b>Other recording</b> are usually intended versions – mark them with
<b>N</b> as “Not a duplicate” and they will not show up again. Under “Status: Not a duplicate”,
“Back to the list” brings a pair back.</li>
</ul>

<h3>Reading the similarity</h3>
<p>Measured on real files from my own archive (similarity per second, 1 = identical):</p>
{_TABLE}
<tr><th align="left">Pair</th><th align="right">typical value</th></tr>
<tr><td>same recording (also different encoding/bitrate)</td><td align="right">0.90 – 0.98</td></tr>
<tr><td>other recording of the same song</td><td align="right">0.70 – 0.85</td></tr>
<tr><td>different songs</td><td align="right">below 0.45</td></tr>
</table>
<p>From 0.85 a passage counts as the same (MP3 green, FLAC sky blue, other formats pink). The
“Same” column shows which share of the file occurs, sounding the same, in the other one – for an
excerpt the share of the shorter file. Hover over a waveform to see the exact value.</p>

<h3>Keyboard</h3>
<ul>
<li><b>Space</b> play/pause · <b>Tab</b> switch A/B · <b>A</b> / <b>B</b> choose directly</li>
<li><b>←</b> / <b>→</b> 5 seconds back/forward</li>
<li><b>N</b> not a duplicate · <b>Ctrl+Z</b> undo</li>
<li>Mouse wheel in the waveform = zoom, drag = scroll, double-click = show all</li>
<li><b>Right-click</b> in a waveform opens the editor for that file</li>
</ul>

<h3>More tips</h3>
<ul>
<li>“Tempo/pitch” makes matching about twenty times slower. To find the clear duplicates first,
switch it off for the first run and on again later – then only the variants are searched.</li>
<li>“Same titles” also finds very old or heavily edited recordings where the sound fingerprint
does not work.</li>
<li>Moved files are recognised by name and duration and keep their fingerprint and review status.</li>
<li>The <b>editor</b> (right-click in a waveform) cuts losslessly: trim start/end, remove sections,
split into several parts, change tags and file names. Passages that sound different in the other file
are marked red at the top. After saving, the new files are compared with the partner right away and
appear as a new pair; matching against the rest of the archive follows with the next “Continue
analysis”. The original goes to the recycle bin as »(Original) name« – Ctrl+Z brings it back.</li>
<li>Several archives can be kept in separate databases (File menu).</li>
<li>Change the language under Help → “Sprache · Language · Langue”; once a day FlacDup checks whether
a new version is available (can be switched off in the Help menu).</li>
</ul>
"""

TIPS_FR = f"""
<h3>Les types dans la liste</h3>
{_TABLE}
<tr><td><b style="color:#48ba68">Identique</b></td><td>le même enregistrement, presque entier dans les deux fichiers</td></tr>
<tr><td><b style="color:#78c896">Extrait</b></td><td>un fichier est (presque) entièrement contenu dans l’autre – radio edit, version raccourcie</td></tr>
<tr><td><b style="color:#5aaadc">Tempo/hauteur</b></td><td>le même enregistrement plus rapide/lent ou à une autre hauteur</td></tr>
<tr><td><b style="color:#debe46">Parties communes</b></td><td>remix, version longue, medley</td></tr>
<tr><td><b style="color:#c88cdc">Autre enregistrement</b></td><td>même morceau, mais live, réenregistré ou dans une autre langue</td></tr>
<tr><td><b style="color:#8c8c8c">Faible</b></td><td>seulement des ressemblances courtes ou superposées – en général pas un doublon (masqué par défaut)</td></tr>
</table>
<p><b style="color:#ff5c50">Texte en rouge</b> pour le groupe ou le titre quand
l’interprète ou le titre principal (sans ajouts entre parenthèses comme « (xyz mix) ») des deux
fichiers diffèrent – à regarder de près.</p>

<h3>Le plus rapide pour faire le ménage</h3>
<ul>
<li>Régler d’abord le filtre « Type » sur <b>Identique</b> – ce sont les doublons évidents. Trier par
la colonne <b>Dossier</b> pour regrouper les doublons des mêmes albums.</li>
<li>Ensuite <b>Extrait</b> et <b>Tempo/hauteur</b> : l’écoute A/B aide à choisir la version à garder.</li>
<li><b>Parties communes</b> et <b>Autre enregistrement</b> sont souvent des versions voulues – les
marquer avec <b>N</b> comme « Pas un doublon », elles ne réapparaîtront plus. Sous « Statut : Pas un
doublon », « Remettre dans la liste » ramène une paire.</li>
</ul>

<h3>Lire la concordance</h3>
<p>Mesuré sur de vrais fichiers de ma collection (concordance par seconde, 1 = identique) :</p>
{_TABLE}
<tr><th align="left">Paire</th><th align="right">valeur typique</th></tr>
<tr><td>même enregistrement (aussi autre encodage/débit)</td><td align="right">0,90 – 0,98</td></tr>
<tr><td>autre enregistrement du même morceau</td><td align="right">0,70 – 0,85</td></tr>
<tr><td>morceaux différents</td><td align="right">moins de 0,45</td></tr>
</table>
<p>À partir de 0,85 un passage est considéré comme identique (MP3 vert, FLAC bleu ciel, autres formats
rose). La colonne « Pareil » indique quelle part du fichier se retrouve, à l’identique, dans l’autre –
pour un extrait, la part du fichier le plus court. Survoler une forme d’onde affiche la valeur exacte.</p>

<h3>Clavier</h3>
<ul>
<li><b>Espace</b> lecture/pause · <b>Tab</b> basculer A/B · <b>A</b> / <b>B</b> choisir directement</li>
<li><b>←</b> / <b>→</b> 5 secondes en arrière/en avant</li>
<li><b>N</b> pas un doublon · <b>Ctrl+Z</b> annuler</li>
<li>Molette dans la forme d’onde = zoom, glisser = défiler, double-clic = tout afficher</li>
<li><b>Clic droit</b> dans une forme d’onde ouvre l’éditeur pour ce fichier</li>
</ul>

<h3>Autres conseils</h3>
<ul>
<li>« Tempo/hauteur » rend le rapprochement environ vingt fois plus long. Pour trouver d’abord les
doublons évidents, le désactiver au premier passage et le réactiver plus tard – seules les variantes
sont alors recherchées.</li>
<li>« Mêmes titres » trouve aussi des enregistrements très anciens ou très retravaillés pour lesquels
l’empreinte sonore ne fonctionne pas.</li>
<li>Les fichiers déplacés sont reconnus par leur nom et leur durée et gardent leur empreinte et leur
statut.</li>
<li>L’<b>éditeur</b> (clic droit dans une forme d’onde) coupe sans perte : raccourcir début/fin, retirer
des passages, découper en plusieurs parties, modifier tags et noms de fichiers. Les passages qui sonnent
différemment dans l’autre fichier sont marqués en rouge en haut. Après l’enregistrement, les nouveaux
fichiers sont aussitôt comparés au partenaire et apparaissent comme nouvelle paire ; le rapprochement avec
le reste de la collection suit au prochain « Poursuivre l’analyse ». L’original part à la corbeille sous
»(Original) nom« – Ctrl+Z le récupère.</li>
<li>Plusieurs collections peuvent être gérées dans des bases séparées (menu Fichier).</li>
<li>La langue se change sous Aide → « Sprache · Language · Langue » ; une fois par jour, FlacDup vérifie
s’il existe une nouvelle version (désactivable dans le menu Aide).</li>
</ul>
"""

ABOUT_HTML = by_lang(ABOUT_DE, ABOUT_EN, ABOUT_FR)
TIPS_HTML = by_lang(TIPS_DE, TIPS_EN, TIPS_FR)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(T("Über {app} {version}", app=APP_NAME, version=VERSION))
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
        title_box.addWidget(QLabel(T("Version {version} · Audio-Dublettenfinder", version=VERSION), self))
        header.addSpacing(8)
        header.addLayout(title_box)
        header.addStretch(1)
        layout.addLayout(header)

        tabs = QTabWidget(self)
        for label, html in ((T("Über {app}", app=APP_NAME), ABOUT_HTML), (T("Tooltipps"), TIPS_HTML)):
            browser = QTextBrowser(self)
            browser.setOpenExternalLinks(True)
            browser.setHtml(html)
            tabs.addTab(browser, label)
        self.tabs = tabs
        layout.addWidget(tabs, stretch=1)

        buttons = QHBoxLayout()
        donate = QPushButton("♥  " + T("Spenden via PayPal"), self)
        donate.setToolTip(DONATE_URL)
        donate.setStyleSheet("QPushButton { padding: 6px 14px; font-weight: bold; }")
        donate.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DONATE_URL)))
        web = QPushButton("www.undabtanzbar.de", self)
        web.setToolTip(WEBSITE_URL)
        web.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(WEBSITE_URL)))
        close_btn = QPushButton(T("Schließen"), self)
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(donate)
        buttons.addWidget(web)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)
