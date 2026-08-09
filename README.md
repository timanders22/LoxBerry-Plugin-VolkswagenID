# LoxBerry-Plugin: Volkswagen ID

Bindet **Volkswagen-Fahrzeuge** über das Volkswagen-Konto an Loxone an:
Ladezustand, Reichweite, Kilometerstand, Verriegelung, Türen, Fenster, Licht,
Handbremse, Fahrzeugzustand, Klimatisierung, Scheibenheizung, Ladewerte,
Standort sowie Inspektions- und Ölservice-Fristen. Auf Wunsch lassen sich
Klimatisierung, Ladevorgang, Ladegrenze, Ladestrom und Scheibenheizung
schalten.

Gebaut für die **ID-Reihe** (ID.3, ID.4, ID.5, ID.7, ID.Buzz). Andere
vernetzte Volkswagen funktionieren ebenfalls — dann bleiben die rein
elektrischen Werte leer und die des Verbrenners sind belegt. Bei einem
Plug-in-Hybrid führt das Plugin beide.

> **Fassung 0.9.1 — ungeprüft.** Das Plugin wurde ohne Volkswagen-Konto und
> ohne Fahrzeug gebaut. Ob die Anmeldung gelingt, ob ein bestimmtes Fahrzeug
> alle abgefragten Werte liefert und ob die schreibenden Befehle die erwartete
> Wirkung haben, ist **nicht** geprüft. Alles übrige ist es — und zwar nicht
> gegen Attrappen, sondern gegen **echte Objekte der Bibliothek**. Deshalb
> 0.9.x und nicht 1.0.0, und deshalb sind schreibende Befehle ab Werk gesperrt.

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
