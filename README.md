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

> **Fassung 0.9.0 — ungeprüft.** Das Plugin wurde ohne Volkswagen-Konto und
> ohne Fahrzeug gebaut. Ob die Anmeldung gelingt, ob ein bestimmtes Fahrzeug
> alle abgefragten Werte liefert und ob die schreibenden Befehle die erwartete
> Wirkung haben, ist **nicht** geprüft. Alles übrige ist es — und zwar nicht
> gegen Attrappen, sondern gegen **echte Objekte der Bibliothek**. Deshalb
> 0.9.0 und nicht 1.0.0, und deshalb sind schreibende Befehle ab Werk gesperrt.
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

## Lizenz

MIT — siehe [LICENSE](LICENSE). Die Anbindung nutzt
[carconnectivity](https://github.com/tillsteinbach/CarConnectivity) und den
[Volkswagen-Connector](https://github.com/tillsteinbach/CarConnectivity-connector-volkswagen)
(ebenfalls MIT). Das ist keine amtliche Volkswagen-Schnittstelle: Volkswagen
kann sie ohne Ankündigung ändern, womit dieses Plugin unbrauchbar würde. Das
Projekt ist weder mit der Volkswagen AG verbunden noch von dort unterstützt.
