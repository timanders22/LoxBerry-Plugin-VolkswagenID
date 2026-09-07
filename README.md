# LoxBerry-Plugin: Volkswagen ID

Bindet **Volkswagen-Fahrzeuge** über das Volkswagen-Konto an Loxone an:
Ladezustand, Reichweite, Kilometerstand, Verriegelung, Türen, Fenster, Licht,
Handbremse, Fahrzeugzustand, Klimatisierung, Scheibenheizung, Ladewerte,
Standort sowie Inspektions- und Ölservice-Fristen. Auf Wunsch lassen sich
Klimatisierung, Ladevorgang, Ladegrenze, Ladestrom und Scheibenheizung
schalten — und hinter einem zweiten Haken auch verriegeln, entriegeln,
hupen und blinken.

Gebaut für die **ID-Reihe** (ID.3, ID.4, ID.5, ID.7, ID.Buzz). Andere
vernetzte Volkswagen funktionieren ebenfalls — dann bleiben die rein
elektrischen Werte leer und die des Verbrenners sind belegt. Bei einem
Plug-in-Hybrid führt das Plugin beide.

> **Fassung 0.9.x — ungeprüft.** Das Plugin wurde ohne Volkswagen-Konto und
> ohne Fahrzeug gebaut. Ob die Anmeldung gelingt, ob ein bestimmtes Fahrzeug
> alle abgefragten Werte liefert und ob die schreibenden Befehle die erwartete
> Wirkung haben, ist **nicht** geprüft. Alles übrige ist es — und zwar nicht
> gegen Attrappen, sondern gegen **echte Objekte der Bibliothek**. Deshalb
> 0.9.x und nicht 1.0.0, und deshalb sind schreibende Befehle ab Werk gesperrt.

## Neu in 0.9.16

- **Nur Schreibweise.** Die Sprachdateien führten für sichtbare Zeichen
  noch HTML-Entitäten (`&mdash;`, `&auml;`, `&bdquo;`); jetzt stehen dort die
  Zeichen selbst — in dieser Fassung **339** Stück. Das ist der Hausbeschluss
  vom 14.08.2026: mit direkten Zeichen darf `htmlspecialchars` folgenlos
  zweimal laufen, und die Doppelmaskierung fällt als Fehlerklasse weg.
  `&nbsp;` und `&shy;` bleiben Entität (unsichtbares Zeichen im Quelltext ist
  eine Wartungsfalle), ebenso die bedeutungstragenden `&amp;`, `&lt;`, `&gt;`,
  `&quot;` und `&apos;`. **Am Verhalten ändert sich nichts.**

## Neu in 0.9.15

### Der Dienst konnte sein Protokoll verlieren, ohne dass es auffiel

`log/plugins` liegt auf einer Ramdisk (`/dev/zram0`). Wird sie geleert — beim
Neustart, durch LoxBerrys `log_maint`, oder von Hand —, ist die Datei fort. Ein
`RotatingFileHandler`, der sie beim Start **einmal** geöffnet hat, schreibt
danach bis zum nächsten Neustart in einen gelöschten Inode: keine
Fehlermeldung, keine Datei, kein Hinweis. Auch die Rotation greift dann nicht
mehr.

Diese Fassung benutzt deshalb `WachsameRotation` in `bin/vw.py` — einen
umlaufenden Handler, der vor jeder Zeile Gerätenummer und Inode vergleicht und
nötigenfalls neu öffnet. Die Standardbibliothek hat für den einen Fall den
`WatchedFileHandler` und für den anderen den `RotatingFileHandler`, aber
nichts, was beides kann; deshalb die eigene Klasse.

Auf dem LoxBerry geeicht, vier Prüfungen und in beide Richtungen: schreiben,
nach dem Löschen weiterschreiben, Umlauf bei Überlänge, nach dem Umlauf erneut
löschen. Mit dem alten Handler ist die Zeile nach dem Löschen verloren und
bleibt es, mit dem neuen steht sie in der wieder angelegten Datei. Auf einem
Windows-Arbeitsplatz lässt sich das nicht messen — dort kann eine offene Datei
gar nicht gelöscht werden.

Aufgefallen ist die Bauart am Heimkino-Plugin, dessen Dienst sieben Stunden
ohne Protokolldatei lief, und am laufenden Gerät belegt: der
Midea2Lox-Dienst hielt `midea2lox.log (deleted)` offen, während unter
demselben Namen längst eine neue Datei fortgeschrieben wurde — von außen sah
das Plugin gesund aus. Elf Linien tragen dieselbe Bauart; alle elf sind am
06.09.2026 nachgezogen worden.

**Die zweite Hälfte gehört dem Startskript.** `bin/dienst.sh` hängte die
Ausgabe des Dienstes mit `nohup … >> "$LOGDATEI"` an **dieselbe** Datei, die
der Handler führt. Damit hält die Shell einen zweiten, anhängenden Deskriptor
darauf — und der bleibt auf der gelöschten Datei stehen, gleich wie gut das
Programm nachfasst. Am Gerät gemessen (06.09.2026): sieben laufende Dienste
hielten so eine gelöschte Protokolldatei offen. Die Ausgabe geht jetzt in
`vw_start.log`, das bei jedem Start geleert wird; das Protokoll gehört
allein dem Handler. Übernommen von AnkerSolix, das es seit 0.9.6 so macht.

Im Sandkasten am Gerät geprüft, in beide Richtungen: mit dem alten Skript
steht die Dienstausgabe im Protokoll und es gibt keine Startdatei, mit dem
neuen ist es umgekehrt — Start, Startdatei, unberührtes Protokoll und Stopp
je sechs von sechs.


## Neu in 0.9.14

### Die Sitzheizung wird nur noch gelesen

Bis 0.9.13 bot dieses Plugin vier Ja/Nein-Schalter an, darunter die
Sitzheizung. **Am Gerät gemessen** (06.09.2026, Volkswagen-Connector 0.10.6):
der Connector macht zwölf Fahrzeugattribute schreibbar, und `seat_heating` ist
nicht darunter. Es ist dort kein eigener Schalter, sondern ein *abgeleiteter*
Anzeigewert — wahr, sobald eine der vier Sitzzonen läuft. Ein Schaltversuch
lief in eine Fehlermeldung der Bibliothek
(`TypeError: … Attribute is not mutable`).

Seit 0.9.14 gilt:

* **Schaltbar sind drei**: Klimatisierung beim Entriegeln, Klimatisierung ohne
  Netz, Stecker automatisch entriegeln.
* **Die Sitzheizung wird weiterhin gelesen** — Thema
  `<präfix>/fahrzeugN/sitzheizung_ein` und Feld `SITZH` der Statusantwort
  bleiben unverändert.
* Wer die Schaltadresse im Miniserver eingetragen hat, bekommt keine
  Fehlermeldung „unbekannt", sondern **den Grund im Klartext**
  (`GRUND=NUR_LESEND`) und die Liste dessen, was schaltbar ist. Der Name
  verschwindet also nicht stillschweigend.

### Lange Werte werden nicht mehr abgeschnitten

Was das Plugin an den UDP-Eingang des MQTT-Gateways schickt, war auf **200
Zeichen** gekürzt. Die Zahl stammte aus einem Schwesterplugin und war im
Quelltext selbst als ungemessen gekennzeichnet. Sie traf Anschriften und
Ladesäulennamen: Wer eine lange Zieladresse gesetzt hatte, bekam sie in Loxone
abgeschnitten zu sehen.

Nachgemessen am 06.09.2026, auf beiden Wegen:

* **An der Quelle** — `sbin/mqttgateway.pl` des LoxBerry, Zeile 92:
  `my $udpMAXLEN = 10240;`. So groß ist der Puffer, mit dem der UDP-Eingang
  liest.
* **In der Wirkung** — Nutzlasten von 50, 200, 400, 900, 3 000 und 9 000 Byte
  kamen alle vollständig und ungekappt im Broker an.

Die neue Grenze ist **1024 Zeichen**, nicht 10 240: im selben Datagramm liegen
auch Befehlswort und Thema, und ein Zeichen kann in UTF-8 bis zu vier Byte
belegen — 1024 Zeichen sind damit höchstens rund 4 kB und bleiben deutlich
unter der Puffergrenze. Was Loxone selbst an einem Textbaustein annimmt, ist
hier **nicht** gemessen.

## Neu in 0.9.13

- **Der Reiter Test sagt jetzt, ob die MQTT-Veröffentlichung dieses Plugins
  eingeschaltet ist.** Bis 0.9.12 stand dort nur der Zustand des MQTT-Gateways
  von LoxBerry — das ist eine Aussage über den LoxBerry, nicht über dieses
  Plugin. Wer die Veröffentlichung ausgeschaltet hatte, sah trotzdem einen
  grünen Haken und konnte am Reiter nicht erkennen, dass nichts an den Broker
  geht. Die neue Zeile steht vor der Gateway-Zeile und ist **grau**, wenn
  ausgeschaltet — das ist eine Entscheidung, kein Fehler. Anlass: derselbe
  Befund an BatterieBMS 0.9.17, dort am Gerät gemessen (`Regeln/04`).

- **Das Auswahlfeld zeichnet seinen Pfeil selbst.** Bis 0.9.12 kam er von der
  Oberfläche des LoxBerry. Am 05.09.2026 am Gerät gemessen (LoxBerry 4.0.0.15,
  `system/css/components.css`): deren Regel `.lb-content select`
  gibt es erst seit der neuen Oberfläche, und jede eigene Feldregel mit der
  Kurzform `background:` löscht sie wieder. Darauf soll sich eine
  Plugin-Oberfläche nicht verlassen (`Regeln/04`). Sonst ist an dieser
  Fassung nichts geändert.

## Was 0.9.10 ändert

Die größte Fassung seit dem ersten Release: zwölf Befunde behoben und rund
sechzehn Erweiterungen. Alle Befunde sind an einem Prüfstand mit `php -S` und
einer LoxBerry-Attrappe **gemessen**, nicht aus dem Quelltext erschlossen.

### Behoben

**Formulare tragen jetzt ein Merkmal gegen fremde Absender.** `htmlauth`
schützt gegen den unangemeldeten Aufruf, nicht dagegen, dass der Browser eines
angemeldeten Bedieners ein Formular abschickt, das auf einer fremden Seite
steht. Gemessen: ein einziger fremder POST erzeugte ein neues Aktionstoken
(danach beantwortet der Endpunkt jeden Virtuellen Ausgang mit 403, und ein
Virtueller Ausgang wertet die Antwort nicht aus — der Ausfall bleibt still),
ein zweiter legte einen Klimabefehl in die Warteschlange, ein dritter löschte
die Volkswagen-Zugangsdaten samt Zweitschrift.

**Eine beschädigte Konfiguration reißt die Zweitschrift nicht mehr mit.** Die
Selbstheilung prüfte bisher nur auf leer und `{}`. Eine beim Schreiben
abgeschnittene Datei — Stromausfall — ist keins von beidem; es galt dann die
Werkseinstellung mit leerem Token, und das erste Öffnen der Oberfläche schrieb
ein neu erzeugtes Token über die intakte Zweitschrift. Gemessen gingen dabei
Takt, Thema, Steuerungshaken und alle Loxone-Adressen verloren. Die
Zweitschrift wird jetzt **gelesen**, nicht kopiert, und die beschädigte Datei
bleibt als `vw.json.kaputt` liegen.

**Nach dem Zurückspielen zeigt die Oberfläche den neuen Stand.** Der Handler
stand hinter dem Laden der Anzeigewerte: die Datei trug danach die neuen
Werte, die Seite zeigte neunzehnmal das alte Aktionstoken und jedes Feld auf
altem Stand. Wer daraufhin auf *Speichern* drückte, schrieb den alten Stand
zurück.

**Die Sicherungsdatei wird Wert für Wert geprüft**, nicht nur Schlüssel für
Schlüssel. Eine Datei mit elf bekannten Schlüsseln und elf unsinnigen Werten
wurde bisher mit „11 Werte übernommen" quittiert.

**Der unangemeldete Endpunkt schreibt nichts mehr.** Ein Aufruf ohne Token,
korrekt mit 403 beantwortet, hat bisher die Konfigurationsdatei aus der
Zweitschrift zurückgeschrieben.

**Ein Tippfehler in der Fahrgestellnummer schaltet nicht mehr das falsche
Auto.** `int("WVW…")` warf, und der Auffangzweig nahm Fahrzeug 1 — bei zwei
Fahrzeugen startete die Klimatisierung damit am falschen und meldete `OK=1`.

**Ein einziger fehlgeschlagener Abruf sperrt nicht mehr alle Schaltbefehle.**
Die Fahrzeugliste wurde bei jedem Durchgang geleert; nach einer Netzstörung
meldete jeder Befehl minutenlang „Es ist noch kein Fahrzeug bekannt".

**`"0"` als Zeichenkette öffnet das Schreibtor nicht mehr.** `bool("0")` ist in
Python wahr, `empty("0")` in PHP ebenfalls — ein solcher Wert in `vw.json` ließ
den Dienst schalten, während Oberfläche und Endpunkt „gesperrt" anzeigten.

**Der Schreibweg hat jetzt eine Zeitgrenze.** Ein Schreibbefehl ist keine
Zuweisung, sondern eine blockierende HTTP-Anfrage von bis zu neun Minuten.

**Keine Neustartschleife mehr** bei fehlenden Zugangsdaten oder vollem
Datenträger: der Sollmerker wird mitgenommen, und `dienst.sh` prüft die
Zugangsdatei auf Inhalt statt nur auf Vorhandensein.

**Der Selbsttest meldet MQTT nicht mehr als Fehler**, wenn MQTT ausgeschaltet
ist. **Die Ladegrenze** liest ihre Grenzen aus der Bibliothek, statt 10 bis 100
zu behaupten und den Anwender in ein rohes `ValueError` laufen zu lassen.

### Neu

* **Vorlagen für alles**: fünf Eingangsvorlagen (Status, Laden, Wartung,
  Position, Verbrauch), eine **Ausgangsvorlage** mit den schaltenden Befehlen
  und eine MQTT-Vorlage — je Fahrzeug. Bisher gab es genau eine.
  Die Ausgangsvorlage führt ab Werk **zwölf** der sechzehn Befehle; Ver- und
  Entriegeln, Hupe und Lichthupe kommen erst dazu, wenn der zweite Haken
  gesetzt ist. Seit 0.9.12 hat jeder Ja/Nein-Schalter einen eigenen Ausgang —
  vorher war nur die Sitzheizung verdrahtet. Seit 0.9.14 sind es **drei**
  Schalter, siehe unten.
* **Lebenszeichen**: ein umlaufender Zähler 0…999 in jeder Statuszeile und über
  MQTT, dazu `ts` und `FEHLFOLGE`. Anders als `ALTER` übersteht ein Zähler den
  Zeitsprung, den ein Raspberry ohne Echtzeituhr beim ersten Zeitabgleich macht.
* **Rund fünfzig neue Werte**, alle aus Daten, die der Dienst ohnehin las:
  Batterietemperatur, WLTP-Reichweite, AdBlue, Ladesäule mit Name und Betreiber,
  Einzeltüren und -fenster mit Namen, Standzeit, Höhe — dazu sechzehn Textthemen
  über MQTT.
* **Entfernung und *zuhause*** aus einem hinterlegten Heimatort.
* **Ladeprotokoll und Verbrauch** in einem eigenen Reiter *Verlauf*, mit
  Tagwahl. Aufbewahrt werden ab Werk **acht** Tage (Reiter *Einstellungen*,
  einstellbar von 1 bis 90); die Tagwahl zeigt höchstens vierzehn davon an.
* **Drosselung**: Mindestabstand für Sofortabrufe, Befehle je Stunde und eine
  Entprellung. Beim Überschussladen liefert Loxone denselben Sollwert im
  Sekundentakt — ohne Entprellung wären das dreitausend Schreibbefehle je Stunde.
* **Ver- und Entriegeln, Hupe und Lichthupe** hinter einem zweiten Haken.
* **Ladeempfehlung** aus einem fremden MQTT-Thema und **Vorklimatisierung** zur
  Abfahrt.
* **`retain`** für den MQTT-Weg, ein **Healthcheck** für das
  Benachrichtigungszentrum des LoxBerry und **neun neue Prüfzeilen** im Reiter
  Test — darunter ein echter HTTP-Aufruf des eigenen Endpunkts und ein Abgleich
  der Themenliste gegen den Sendecode.

## Was 0.9.8 ändert

Vier Korrekturen, alle aus einem Zeile-für-Zeile-Vergleich mit den
Schwesterplugins. Die erste verlangt **einen Handgriff in Loxone Config**;
die dritte schließt eine Lücke, in der bisher gar nicht geprüft wurde.

### Der Suchtext des Kilometerstands war zweideutig — bitte nachtragen

Der virtuelle Eingang für `KM` trug bisher den Suchtext `\iKM=\i\v`. Die
Antwort des Wartungs-Abrufs lautet aber

    WARTUNG;OK=1;INSPTAGE=…;INSPKM=15000;OELTAGE=…;OELKM=…;KM=48210;ALTER=…

und Loxone nimmt die **erste** Fundstelle: `INSPKM=`. Der Kilometerstand las
damit die Inspektionsvorgabe — im Beispiel 15 000 statt 48 210. Beide Zahlen
sehen aus wie ein Kilometerstand, der Fehler meldet sich nicht.

Alle Suchtexte tragen jetzt das Semikolon: `\i;KM=\i\v`. Sie entstehen dazu
an **einer** Stelle im Quelltext statt an fünf — genau diese Verdopplung hatte
die Regel auseinanderlaufen lassen.

> **Was Sie tun müssen:** Wer die Importdatei neu erzeugt, bekommt die
> berichtigten Suchtexte automatisch. Wer die Eingänge behalten will, ändert in
> Loxone Config bei den **drei Eingängen mit `KM` im Namen** (`INSPKM`, `OELKM`,
> `KM`) den Suchtext von `\iNAME=` auf `\i;NAME=`. Bei allen anderen Eingängen
> wirkt sich das Semikolon nicht aus — es schadet aber auch dort nicht, und der
> Reiter *Einbindung in Loxone* zeigt jetzt überall die Fassung mit Semikolon.

### Jede Eingangsvorlage hat ihren eigenen Namensraum

Bis 0.9.11 erzeugten die fünf Eingangsvorlagen zusammen 73 Eingänge unter nur
59 verschiedenen Namen: `VW_1_OK` und `VW_1_ALTER` standen in allen fünf,
`VW_1_SOC`, `VW_1_KM`, `VW_1_BATTTEMP`, `VW_1_ENTFERNUNG`, `VW_1_ZUHAUSE` und
`VW_1_VERBRAUCH` in je zweien. Wer zwei Vorlagen einlas, bekam gleichnamige
Befehlserkennungen, und die Baustein-Liste dieses Plugins konnte nicht sagen,
welche gemeint war.

Seit 0.9.12 trägt jede Vorlage außer der Statusvorlage ein Kürzel:
`VW_1_LD_SOC` (Laden), `VW_1_WA_KM` (Wartung), `VW_1_PO_ZUHAUSE` (Position),
`VW_1_VB_VERBRAUCH` (Verbrauch). **Die Statusvorlage behält alle bisherigen
Namen unverändert** — wer nur sie eingelesen hat, muss nichts nachziehen.
Wer eine der vier anderen Vorlagen neu einliest, bekommt die neuen Namen und
zieht die Verwendung in der Programmierung einmal nach.

### Nach einer Aktualisierung läuft der Dienst wieder

`preupgrade.sh` hält den Dienst an. Ein Merker **neben** dem
Konfigurationsordner sagt dem `postinstall.sh`, dass er lief, und der startet
ihn wieder — sofort und ohne Umweg. Der Merker wird nur gesetzt, wenn der
Vorgang wirklich lief, und in jedem Fall wieder entfernt.

> **Berichtigung vom 03.09.2026.** An dieser Stelle stand seit dem 20.08.2026,
> `purge_installation` laufe „ausschließlich beim Deinstallieren (`:233`)", der
> Sollmerker `soll_laufen` überlebe das Upgrade und der Cron-Wächter hole den
> Dienst von selbst zurück. **Das war falsch.** Nachgemessen an der Quelle
> selbst — `sbin/plugininstall.pl`, Zweig `master`, 2054 Zeilen:
>
> ```
> 233:	&purge_installation("all");     <- Deinstallation
> 886:		&purge_installation;        <- IM UPGRADE-ZWEIG
> ```
>
> Zeile 886 steht innerhalb von `if ($isupgrade) {` (`:858`), direkt nach den
> `preupgrade`-Skripten. Im Rumpf, unter `if ($pfolder)` und **ohne** Prüfung
> auf `"all"`, steht `rm -rf` auf `config/plugins/<ordner>/` **und**
> `data/plugins/<ordner>/`. Der Sollmerker überlebt das Upgrade also **nicht**,
> und der Wächter holt nichts zurück: der Merker neben dem Konfigurationsordner
> ist der einzige Weg, und er behebt sehr wohl einen Stillstand.

`preupgrade.sh` legt jetzt einen Merker **neben** dem Konfigurationsordner ab —
denn auch der wird ausgeräumt — und zwar nur dann, wenn der Vorgang wirklich
lief. `postinstall.sh` startet danach und entfernt den Merker in jedem Fall,
auch wenn der Start scheitert; ein liegengebliebener Merker hätte den Dienst
beim nächsten Upgrade ungefragt gestartet.

### Die Reiter werden jetzt geprüft — vorher tat es niemand

Die Reiterleiste entsteht in einer Schleife über `$vw_reiter_ids`. Das ist die
richtige Lösung: die Namen stehen nur einmal da. Nur sucht
`hausstandard_pruefen.py` die Reiter als wörtliche Zeichenketten im Quelltext
und findet in einer erzeugten Leiste keine — die Spalte `tab` blieb ein
**Strich**. Ein Strich liest sich wie „nichts zu beanstanden", er heißt aber
„nichts gemessen". Einen eigenen Test dafür gab es nicht.

Der Reiter *Test* prüft es jetzt selbst, am Quelltext der Oberfläche, und nennt
drei Fälle beim Namen: eine Fläche ohne Eintrag in der Liste (der Reiter ist
unerreichbar und die Seite springt nach jedem Absenden zurück), ein Eintrag
ohne Fläche (der Reiter bleibt leer) und ein Eintrag ohne Beschriftung. Die
Leiste selbst wird **nicht** verglichen, und das ist keine Lücke: sie entsteht
aus derselben Liste. Geeicht mit
`Werkzeuge/reiterpruefung_eichung.py` — alle drei Fälle werden rot, und zwar
mit dem richtigen Grund.

### Baustein #14 hatte vier Eingänge

Bei `UND` und `ODER` ist die Zahl der Eingänge eine Eigenschaft des Bausteins,
die Loxone Config selbst setzt. Wer einen dritten Eingang aufzieht, verliert
beim nächsten Öffnen der Datei **alle Verbindungen, die daran hingen** — ohne
Meldung. Die Baustein-Liste im Reiter *Einbindung in Loxone* nennt für #14
jetzt zwei Eingänge mit je zwei Quellen; an einem ODER werden mehrere Quellen
an einem Eingang ODER-verknüpft, das Ergebnis ist dasselbe. Bei #30 (ein UND)
hängt weiterhin genau eine Quelle je Eingang — dort wäre dieselbe Form falsch,
weil sie das UND still in ein ODER verwandelte.

## Was 0.9.1 ändert

**Der Plugin-Ordner wird ermittelt, nicht geraten.** `vw_paths()` fiel auf den
festen Namen `volkswagenid` zurück, sobald `config/plugins/<ordner>` noch
fehlte — etwa im Augenblick der Installation. Hängt LoxBerry bei einer
Zweitinstallation einen Zähler an (`volkswagenid_01`), zeigten deren Pfade
damit auf die **erste** Installation: gemeinsame Konfiguration — und darin
stehen Zugangsdaten und Anmeldemarken —, gemeinsame Warteschlange, gemeinsames
Protokoll. Maßgeblich ist jetzt `LBPPLUGINDIR`.

**Eine leere Befehlsdatei konnte in die Warteschlange geraten.**
`vw_befehl_senden()` schrieb `json_encode($befehl)` direkt weiter. Gibt
`json_encode` bei ungültigem UTF-8 `false` zurück, macht `file_put_contents`
daraus eine leere Zeichenkette, schreibt null Byte und meldet **Erfolg** — der
Rückgabewert ist `0`, nicht `false`, die Prüfung auf `=== false` greift also
nicht. `vw_config_write()` im selben Modul macht es seit jeher richtig.

**Im Kommentarkopf von `icons/icon.svg` stand „Skoda Connect"** — ein Rest aus
dem Schwesterplugin.
> Die Selbstaktualisierung zeigt auf dieses Repository; bei gleicher Fassung
> wird niemandem ein Update angeboten.

## Warum carconnectivity und nicht weconnect

Die ältere Bibliothek
[WeConnect-python](https://github.com/tillsteinbach/WeConnect-python) ist vom
eigenen Autor als **End of Life** angekündigt worden; die letzte Fassung
stammt vom 30.11.2025. Nachfolger desselben Autors ist
[carconnectivity](https://github.com/tillsteinbach/CarConnectivity) mit dem
[Volkswagen-Connector](https://github.com/tillsteinbach/CarConnectivity-connector-volkswagen).
Beide werden gepflegt (0.11.10 beziehungsweise 0.10.6, Stand 05.07.2026) und
decken neben Volkswagen weitere Marken ab.

Ein Plugin auf der abgekündigten Bibliothek zu bauen wäre derselbe Fehler
gewesen wie beim alten SkodaConnect-Plugin — nur mit Ansage.

## Zwei Grenzen

* **Europa.** Der Connector spricht mit dem europäischen Dienst
  (`emea.bff.cariad.digital`). Für Nordamerika gibt es einen
  [eigenen Connector](https://github.com/zackcornelius/CarConnectivity-connector-volkswagen-na),
  der hier **nicht** eingebaut ist.
* **Zwei-Faktor-Bestätigung.** Einzelne Konten verlangen sie beim Anmelden.
  Das lässt sich nicht automatisieren; wer betroffen ist, meldet sich einmal
  im Browser auf demselben Gerät an.

## Voraussetzungen

* **Python 3.9 oder neuer.** Das erfüllt jeder LoxBerry, den es heute gibt:
  Debian 12 (Bookworm) liefert 3.11, Debian 13 (Trixie) liefert 3.13. Anders
  als beim Skoda-Plugin gibt es hier also **keine** Hürde.
* **Internetverbindung bei der Installation.** Beide Pakete werden von PyPI
  geholt (festgenagelt auf 0.11.10 und 0.10.6; schlägt das fehl, werden die
  neuesten genommen und das ausdrücklich gemeldet).
* **`python3-venv`.** Systemweites `pip3 install` scheitert auf Debian 12/13 an
  PEP 668 (`externally-managed-environment`); deshalb eine eigene venv unter
  `bin/plugins/volkswagenid/venv`.
* MQTT-Gateway eingeschaltet, wenn die Werte per MQTT kommen sollen. Es ist
  seit LoxBerry 3 Bestandteil des Systems und wird unter *System → MQTT
  Gateway* aktiviert, nicht nachinstalliert.
* **Erreichbarer Namensdienst.** Die Bibliothek fragt beim Start einen
  Zeitserver (`pool.ntp.org`), um vor einer falsch gestellten Systemuhr zu
  warnen. Sie fängt dabei nur NTP-eigene Fehler ab — ein DNS-Fehler bringt
  den Konstruktor sonst zum Absturz. Das Plugin kapselt diesen Aufruf und
  vermerkt den Ausfall im Protokoll, statt daran zu sterben.

## Abholtakt: mindestens 180 Sekunden

Das ist die Untergrenze der Bibliothek, keine Vorsicht dieses Plugins: der
Connector wirft darunter beim Anlegen einen `ValueError`. Das Plugin weist
kleinere Werte deshalb schon in der Oberfläche ab. Fünf Minuten sind ein guter
Anfang.

## Aufbau

    bin/vw.py                 Abrufdienst (Python, eigene venv)
    bin/dienst.sh             Start, Stopp, Wächter
    cron/cron.01min           minütlicher Wächter
    webfrontend/htmlauth/     Bedienoberfläche (fünf Reiter)
    webfrontend/html/         Endpunkt für den Miniserver + gemeinsame Bibliothek

Drei Aufgaben, drei Dateien: Die Oberfläche bedient, der Dienst ruft ab, der
Endpunkt bedient den Miniserver. Weder Oberfläche noch Endpunkt sprechen je
selbst mit Volkswagen — sie lesen den Zwischenspeicher und legen Befehle in
einer Warteschlange ab, die der Dienst im Sekundentakt abarbeitet.

## Zugangsdaten

Die Zugangsdaten des **Volkswagen-Kontos** liegen in
`config/plugins/volkswagenid/zugang.json` mit den Rechten 0600, nicht in der
Konfiguration, die die Oberfläche anzeigt, und nie in der Loxone-Projektdatei.

Nach der ersten Anmeldung legt die Bibliothek Anmeldemarken in
`data/plugins/volkswagenid/token.json` ab (ebenfalls 0600, vom Dienst nach
jedem Schreibvorgang nachgesetzt) und meldet sich damit an, statt jedes Mal
das Passwort zu senden. Nach einem Passwortwechsel sind sie wertlos — dafür
gibt es den Knopf *Anmeldung neu erzwingen*.

Ein S-PIN-Feld gibt es, es wird aber **nicht gebraucht**: Ver- und Entriegeln
sowie Hupe und Lichthupe bietet dieses Plugin bewusst nicht an.

## Endpunkte für Loxone

Alle Aufrufe brauchen das Token aus dem Reiter *Einbindung in Loxone*.
Statt der laufenden Nummer darf überall auch die Fahrgestellnummer stehen
(`fahrzeug=WVW…`).

| Aufruf | Zweck |
|---|---|
| `?token=T&aktion=status&fahrzeug=N` | `VOLKSWAGEN;OK=..;SOC=..;TANK=..;REICHW=..;KM=..;VERR=..;TUEREN=..;FENSTER=..;LICHT=..;HANDBR=..;KLIMA=..;ZIELTEMP=..;AUSSEN=..;SCHEIBE=..;ZUSTAND=..;ERREICH=..;ALTER=..` |
| `?token=T&aktion=laden&fahrzeug=N` | `LADEN;OK=..;SOC=..;LAEDT=..;LADEKW=..;TEMPO=..;LADEGR=..;LADESTROM=..;KABEL=..;STECKER=..;REICHWBAT=..;FERTIGMIN=..;ALTER=..` |
| `?token=T&aktion=wartung&fahrzeug=N` | `WARTUNG;OK=..;INSPTAGE=..;INSPKM=..;OELTAGE=..;OELKM=..;KM=..;ALTER=..` |
| `?token=T&aktion=position&fahrzeug=N` | `POSITION;OK=..;BREITE=..;LAENGE=..;ALTER=..` plus Anschrift in einer zweiten Zeile |
| `?token=T&aktion=fahrzeuge` | Liste der erkannten Fahrzeuge |
| `?token=T&aktion=roh` | vollständiges Abbild als JSON |
| `?token=T&aktion=klima_start&temp=21` | Klimatisierung starten |
| `?token=T&aktion=klima_stop` | Klimatisierung anhalten |
| `?token=T&aktion=zieltemperatur&temp=21` | Zieltemperatur setzen |
| `?token=T&aktion=laden_start` / `laden_stop` | Ladevorgang starten/anhalten |
| `?token=T&aktion=ladegrenze&prozent=80` | Ladegrenze setzen (10–100) |
| `?token=T&aktion=ladestrom&ampere=16` | Ladestrom setzen (5, 6, 10, 13, 16 oder 32) |
| `?token=T&aktion=scheibe_ein` / `scheibe_aus` | Scheibenheizung |
| `?token=T&aktion=wecken` | Fahrzeug aus dem Ruhezustand holen |
| `?token=T&aktion=abruf` | sofort abrufen statt auf den Takt zu warten |

`ZUSTAND` ist eine Stufe: `0` offline, `1` geparkt, `2` Zündung an, `3` fährt.

**Ein Strich als Wert** heißt: dieser Wert liegt nicht vor. Es wird bewusst
keine 0 gesendet — eine 0 wäre eine stille Falschaussage. Loxone behält dann
den letzten gültigen Wert; deshalb gehören `ALTER` und `OK` immer mit
ausgewertet.

Schaltende Aufrufe antworten mit `SET;OK=…`: `1` angenommen, `0` abgelehnt (mit
Grund), `2` eingereiht, aber innerhalb der Wartezeit ohne Antwort — also
Ergebnis unbekannt.

**Was `OK=1` nicht heißt.** Der Volkswagen-Server hat den Auftrag mit HTTP 200
entgegengenommen. Ob das Fahrzeug ihn ausgeführt hat, zeigt erst der nächste
Abruf. Wer sicher sein will, wertet den zurückgelesenen Zustand aus und nicht
die Antwort auf den Befehl.

## Einheiten

Alle Werte werden in feste Einheiten umgerechnet, bevor sie den Endpunkt
verlassen: Kilometer, Grad Celsius, Kilowatt, km/h, Prozent, Ampere. Die
Bibliothek liefert je nach Kontoeinstellung auch Meilen und Fahrenheit — wer
das nicht umrechnet, sendet irgendwann Meilen an einen Baustein, der Kilometer
erwartet, und niemand sieht es, weil die Zahl plausibel bleibt.

## Was das Plugin nicht kann

* **Ver- und Entriegeln, Hupe und Lichthupe.** Der Connector böte es an, es
  verlangt die S-PIN. Bewusst weggelassen: ohne Fahrzeug lässt es sich nicht
  verantwortungsvoll erproben.
* **Warnleuchten.** Die Bibliothek führt dafür kein Feld.
* **Nordamerikanische Fahrzeuge.** Siehe oben.

## Datenschutz

Es sind keine persönlichen Daten im Plugin enthalten. Zugangsdaten und alle
Einstellungen liegen ausschließlich in der lokalen Konfiguration. Verbindungen
gibt es nur zum Volkswagen-Dienst, zu einem Zeitserver und, bei der
Installation, zu PyPI.

## Fassung 0.9.1 — nachgemessen und korrigiert

Dreizehn Punkte aus einer Durchsicht. Sechs trafen zu, drei teilweise, vier
nicht. Alles wurde am Code nachgestellt, bevor etwas geändert wurde.

### `fetch_all()` konnte den Dienst unbegrenzt anhalten

Trifft zu — und der naheliegende Weg dagegen wirkt nicht. Gemessen gegen ein
Gegenstück, das die Verbindung annimmt und danach schweigt:

| | Dauer |
|---|---|
| `requests.get()` ohne `timeout` | hängt unbegrenzt (nach 8 s von außen abgebrochen) |
| dasselbe mit `socket.setdefaulttimeout(2)` | **hängt ebenfalls unbegrenzt** |
| dasselbe mit `signal.alarm(2)` | 2,0 s, sauberer `ReadTimeout` |

`setdefaulttimeout` greift nicht, weil urllib3 beim Verbindungsaufbau eine
eigene Zeitgrenze angibt und die Vorgabe des Sockets damit überschreibt. Der
Wecker greift — und das Angenehme daran: `requests` deutet den unterbrochenen
Lesevorgang selbst und räumt seine Verbindung ab, die Fehlerbehandlung der
Bibliothek läuft also wie bei jeder anderen Störung.

Der Abruf ist jetzt in eine Klasse `Zeitgrenze` gefasst (180 s). Gegenprobe:
hängender Aufruf nach 2,0 s abgebrochen, kurzer Aufruf ungestört, und nach
dem Block schlägt kein verspäteter Wecker mehr zu.

### Die Prozessprüfung war zu weich — aber anders, als beschrieben

Der Einwand war, `grep -qa "vw.py"` bzw. `strpos($cmd, 'vw.py')` durchsuche
die ganze Kommandozeile und finde deshalb fremde Prozesse. Der Rahmen war
allerdings schon richtig: geprüft wird **nur** die Nummer aus der eigenen
PID-Datei, es wird nichts gesucht. Die Prüfung dient gegen
Nummernwiederverwendung — und dafür war sie zu weich. Nachgestellt:

| Prozess mit der recycelten Nummer | bisher | argumentweise | jetzt |
|---|---|---|---|
| der Dienst selbst | Dienst | Dienst | Dienst |
| `nano /pfad/vw.py` | **Dienst** | **Dienst** | fremd |
| `tail -f /var/log/vw.py.log` | **Dienst** | fremd | fremd |
| zweites Plugin-Exemplar | **Dienst** | fremd | fremd |

Der vorgeschlagene argumentweise Vergleich allein reicht also nicht: ein
Editor führt den vollen Pfad ebenfalls als zweites Argument. Geprüft werden
jetzt **zwei** Dinge — argv[1] ist genau unser Skript, und argv[0] ist ein
Python.

### Weitere zutreffende Punkte

**Antwortdateien** wurden nach dem Lesen nicht gelöscht — `unlink` ergänzt.

**Kein Häkchen zum Löschen der Zugangsdaten.** Es gibt jetzt eines, und es
löscht mehr als das Passwortfeld: `zugang.json`, die Sicherungskopie neben dem
Konfigordner **und** `token.json`. Die Anmeldemarken sind auch ohne Passwort
ein gültiger Zugang zum Konto — ein Löschen, das sie stehen lässt, ist keines.
Gegenprobe: Passwort danach in 0 Dateien auffindbar.

**`vw.json` ohne eigene Rechte.** Jetzt 0600. Darin stehen zwar keine
Passwörter, aber das Token des unangemeldeten Endpunkts — wer es lesen kann,
kann über HTTP das Fahrzeug schalten.

**Protokoll ganz eingelesen.** Der Speicherhinweis war berechtigt, `tail` ist
aber der langsamste der drei Wege (rund 1,9 ms gegen 0,05 ms beim
Rückwärtslesen mit `fseek`). Umgestellt auf `fseek`.

**Sicherungsort beim Upgrade.** Die Sorge war, LoxBerry lösche die
Sicherungen mit dem Konfigordner. Das trifft nicht zu — gelöscht wird
`config/plugins/<ordner>/`, also das *Verzeichnis*, und
`<ordner>.backup.vw.json` liegt daneben. Genau deshalb übersteht die Sicherung
eine Neuinstallation; das ist ihr Zweck.

Beim Prüfen fiel aber etwas Schwereres auf: **es gab kein Uninstall-Skript.**
Die Sicherung mit E-Mail, Passwort und S-PIN des Volkswagen-Kontos wäre nach
dem Deinstallieren für immer auf der Karte liegen geblieben — die Datei ist
nicht umsonst mit 0600 angelegt. `uninstall/uninstall` gibt es jetzt.

### Nebenbefund: `postinstall.sh` lief bei jedem Upgrade zweimal

`postupgrade.sh` rief `postinstall.sh` auf, obwohl der Installer
`postinstall` ohnehin ohne Bedingung ausführt und `postupgrade` erst danach.
`postinstall.sh` legt die virtuelle Umgebung an und holt `carconnectivity`
samt Volkswagen-Connector über pip aus dem Netz — auf einem Raspberry Pi
Minuten, und das doppelt.

### Was nicht zutraf

**`UnboundLocalError` bei `cc.shutdown()`.** Die Zeile
`cc = CarConnectivity(...)` steht in einem **eigenen** `try`, dessen `except`
mit `return 1` endet; das `try` mit dem `finally` beginnt erst danach. Am
Syntaxbaum nachgeprüft: Zuweisung in Zeile 964, `try` mit `finally` ab Zeile
979, Zuweisung liegt davor, eigener `except`-Zweig mit `return`. `cc` kann
nicht ungebunden sein — und `cc.shutdown()` ist im `finally` zusätzlich in
ein eigenes `try` gefasst.

**Zu schwache URL-Prüfung für `miniserver_url`.** Dieses Plugin hat weder ein
Feld `miniserver_url` noch den genannten Ausdruck `#^https?://\S{3,300}$#`.
Der Punkt stammt sichtbar aus der Durchsicht eines anderen Plugins — er nennt
sogar dessen Variablennamen `$sp_url`.

**Komma bei `temp` im Webhook.** Wird bereits ersetzt, in
`webfrontend/html/index.php`:
`$vw_befehl['temp'] = str_replace(',', '.', $vw_temp);`

**Nicht atomares Schreiben der Statusdateien in Python.** Alle JSON-Schreib­vorgänge
in `vw.py` laufen über `json_schreiben()`, und das schreibt seit jeher in eine
`.tmp` und ruft `os.replace`. Nachgeprüft für jede Schreibstelle. Die eine
Datei, die *nicht* atomar geschrieben wurde, liegt auf der **PHP**-Seite:
`vw_zugang_speichern()`. Die schreibt jetzt ebenfalls über temp und `rename` —
und setzt die Rechte 0600 auf der temporären Datei, nicht danach, damit die
Datei mit dem Passwort darin nicht einen Augenblick lang mit 0644 dasteht.

### Zur Docker-Gruppe

Nicht umgesetzt. Der Vorschlag beginnt mit „falls der Dienst oder
Abhängigkeiten zukünftig lokal in Container-Umgebungen ausgeführt werden
sollen" — das tun sie nicht. Dieses Plugin startet keinen Container und
spricht mit keinem Docker-Dienst; es baut eine virtuelle Python-Umgebung und
redet über HTTPS mit Volkswagen. Wer in der Gruppe `docker` ist, kann
Container mit beliebigen Rechten starten und damit faktisch alles auf dem
Gerät tun. Diese Rechte auf Vorrat zu vergeben, für einen Fall, den es nicht
gibt, wäre der falsche Tausch.

## Lizenz

MIT — siehe [LICENSE](LICENSE). Die Anbindung nutzt
[carconnectivity](https://github.com/tillsteinbach/CarConnectivity) und den
[Volkswagen-Connector](https://github.com/tillsteinbach/CarConnectivity-connector-volkswagen)
(ebenfalls MIT). Das ist keine amtliche Volkswagen-Schnittstelle: Volkswagen
kann sie ohne Ankündigung ändern, womit dieses Plugin unbrauchbar würde. Das
Projekt ist weder mit der Volkswagen AG verbunden noch von dort unterstützt.
