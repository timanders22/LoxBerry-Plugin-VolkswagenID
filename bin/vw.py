#!REPLACELBPBINDIR/venv/bin/python3
"""Volkswagen ID - Abrufdienst fuer LoxBerry.

Holt die Werte der Volkswagen-WeConnect-Schnittstelle ueber die freie
Bibliothek "carconnectivity" samt ihrem Volkswagen-Connector, legt sie als
JSON-Zwischenspeicher ab, gibt sie auf Wunsch ueber das LoxBerry-MQTT-Gateway
weiter und arbeitet Schreibbefehle aus einer Warteschlange ab, die der
Loxone-Endpunkt fuellt.

Warum carconnectivity und nicht weconnect: Die aeltere Bibliothek
"WeConnect-python" ist vom selben Autor als End of Life angekuendigt worden.
"carconnectivity" ist ihr Nachfolger, deckt neben Volkswagen weitere Marken ab
und wird gepflegt.

Drei Aufgaben, drei Dateien - dieses Skript ist der Dienst. Die Oberflaeche
(webfrontend/htmlauth/index.php) und der Miniserver-Endpunkt
(webfrontend/html/index.php) rufen es nie direkt auf, sondern lesen den
Zwischenspeicher beziehungsweise legen Befehle ab.

Aufrufe:
    vw.py                 Dienst (Dauerbetrieb)
    vw.py --einmal        ein einzelner Abruf, dann Ende
    vw.py --selbsttest    Pruefungen ohne Netz, Ausgabe als Klartext
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


def lb_wurzel_ermitteln():
    """Den LoxBerry-Wurzelordner ohne festen Systempfad bestimmen.

    Vom eigenen Ablageort aufwaerts, bis ein Verzeichnis gefunden ist, das
    config/plugins, data/plugins UND config/system/general.json traegt.

    Die dritte Bedingung ist die entscheidende. Bis 0.9.23 wurde nur auf
    config/plugins und webfrontend geprueft - und genau diese beiden Ordner
    hinterlaesst ein Pruefstand auf einem Arbeitsrechner. Am 05.09.2026 hat
    eine solche Suche dort C:\\ als "LoxBerry" erkannt und Daten geloescht
    (Regeln/06, "Eine Wurzelsuche ueber config/plugins und data/plugins
    trifft auf einem Pruefrechner das Laufwerk selbst"). Ein LoxBerry hat
    immer config/system/general.json; ein solcher Rest hat sie nie.
    """
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isdir(os.path.join(d, "config", "plugins")) \
                and os.path.isdir(os.path.join(d, "data", "plugins")) \
                and os.path.isfile(os.path.join(d, "config", "system", "general.json")):
            return d
        eltern = os.path.dirname(d)
        if eltern == d:
            break
        d = eltern
    return ""


MQTT_MAX = 1024


def mqtt_wert_saeubern(wert, laenge: int = MQTT_MAX):
    """Einen Wert fuer den UDP-Eingang des MQTT-Gateways unschaedlich machen.

    Das Gateway liest zeilenweise. Ein Zeilenumbruch im Wert zerlegt die
    Uebertragung, und aus den Bruchstuecken bildet das Gateway erfundene
    Themen. Ein Tabulator schadet ebenso, weil Leerzeichen Thema und Wert
    trennt.

    Gekappt wird ab 0.9.10, weil seither auch Text hinausgeht: eine Anschrift
    oder ein Fehlertext hat keine Obergrenze, ein UDP-Datagramm schon.

    SEIT 0.9.14 AM GATEWAY GEMESSEN. Bis 0.9.13 standen hier 200 Zeichen, aus
    MGiSmart uebernommen und im Kommentar selbst als ungemessen gekennzeichnet.
    Am 06.09.2026 an der Quelle nachgesehen - sbin/mqttgateway.pl des
    LoxBerry, Zeile 92, 'my $udpMAXLEN = 10240;' - so gross ist der Puffer, mit dem der
    UDP-Eingang liest. Und in der Wirkung geprueft: Nutzlasten von 50, 200,
    400, 900, 3000 und 9000 Byte kamen alle vollstaendig und ungekappt im
    Broker an. Die alte Grenze war also um Faktor 50 zu streng; Anschriften
    und Ladesaeulennamen wurden abgeschnitten, obwohl sie durchgingen.

    1024 statt 10240, mit Absicht: im selben Datagramm liegen auch Befehlswort
    und Thema, und ein Zeichen kann in UTF-8 bis zu vier Byte belegen -
    1024 Zeichen sind damit hoechstens rund 4 kB und bleiben weit unter der
    Grenze. Was Loxone selbst an einem Textbaustein annimmt, ist hier NICHT
    gemessen.
    """
    text = str(wert)
    for zeichen in ("\r\n", "\r", "\n", "\t"):
        text = text.replace(zeichen, " ")
    while "  " in text:
        text = text.replace("  ", " ")
    text = text.strip()
    if laenge > 0 and len(text) > laenge:
        text = text[:laenge].rstrip()
    return text


# ---------------------------------------------------------------------------
# Pfade aus dem EIGENEN Ablageort ableiten.
#
# Nicht ueber LoxBerry::System: das leitet den Pluginordner aus dem Aufrufort
# ab und liefert bei einem Start aus postinstall.sh oder aus dem Cron ueberall
# Leerstring. Sichtbare Folge waere ein Dienst, der gegen /-Pfade werkelt und
# trotzdem Erfolg meldet.
# ---------------------------------------------------------------------------
SELF = Path(__file__).resolve().parent            # <home>/bin/plugins/<ordner>


def _ist_lbhome(p) -> bool:
    """Sieht dieses Verzeichnis wie ein LoxBerry aus?

    Drei Merkmale, nicht zwei: config/plugins, data/plugins UND
    config/system/general.json (Begruendung in lb_wurzel_ermitteln()).
    """
    try:
        p = Path(p)
        return ((p / "config" / "plugins").is_dir()
                and (p / "data" / "plugins").is_dir()
                and (p / "config" / "system" / "general.json").is_file())
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Wurzel und Ordnername: GELESEN, nicht geraten.
#
# Bis 0.9.23 stand hier
#     LBHOME = SELF.parents[2] if len(SELF.parents) >= 3 and _ist_lbhome(...)
#     ... else Path($LBHOMEDIR) or lb_wurzel_ermitteln() or SELF.parents[2]
#     PNAME  = SELF.name
# Drei Dinge waren daran falsch:
#   1. Gefragt wurden zuerst die drei festen Ebenen, nicht die Umgebung.
#   2. _ist_lbhome() kannte general.json nicht - siehe dort.
#   3. NACH der Suche stand wieder ein fester Rueckfall auf den Ablageort.
#      Genau dieser Rueckfall macht jede Suche wirkungslos: in einem fremden
#      Baum ohne general.json wird er wieder Wurzel (Stand-Protokolle/
#      2026-09-18_Welle1, "Neue Lehre fuer alle H1-Linien"; in WSL gemessen
#      am 24.09.2026, Pruefung-VolkswagenID-0.9.24).
#
# Reihenfolge nach Regeln/03, Stufe 1 ist die Umgebung:
#   Wurzel:      $LBHOMEDIR  ->  Aufwaertssuche (mit general.json)  ->  Abbruch
#   Ordnername:  $LBPPLUGINDIR  ->  Ablageort  ->  der vorgesehene Name
# Die letzte Stufe des Ordnernamens greift nur dort, wo der Ablageort
# nachweislich kein Pluginordner sein kann ("bin", "plugins") - dieselbe
# Abstufung wie vw_paths() in der Oberflaeche.
# ---------------------------------------------------------------------------
def _lbhome_ermitteln() -> Path:
    h = os.environ.get("LBHOMEDIR") or ""
    # Ein gesetztes LBHOMEDIR gilt mit config/plugins UND data/plugins
    # darunter - general.json wird hier nicht verlangt, damit Attrappen ohne
    # sie (Werkzeuge/lb) weiter tragen. Dieselbe Regel wie vw_wurzel_suchen()
    # in bin/dienst.sh und vw_lbhome() in der Oberflaeche.
    if h and os.path.isdir(os.path.join(h, "config", "plugins")) \
            and os.path.isdir(os.path.join(h, "data", "plugins")):
        return Path(h).resolve()
    gefunden = lb_wurzel_ermitteln()
    if gefunden:
        return Path(gefunden)
    sys.stderr.write(
        "FEHLER: Es wurde kein LoxBerry-Wurzelverzeichnis gefunden. LBHOMEDIR "
        "ist nicht gesetzt oder traegt kein config/plugins und data/plugins, "
        "und oberhalb von %s traegt kein Verzeichnis config/plugins, "
        "data/plugins und config/system/general.json. Es wurde nichts "
        "angelegt und nichts gelesen.\n" % SELF)
    raise SystemExit(1)


def _pname_ermitteln() -> str:
    # LBPPLUGINDIR steht am Geraet nicht in jeder Umgebung (Regeln/03, an 43
    # Linien gemessen) - deshalb die zweite Stufe.
    p = (os.environ.get("LBPPLUGINDIR") or "").strip("/")
    if p and p not in ("bin", "plugins"):
        return p
    if SELF.name not in ("", "/", "bin", "plugins"):
        return SELF.name
    return "volkswagenid"


LBHOME = _lbhome_ermitteln()
PNAME = _pname_ermitteln()
NICHT_INSTALLIERT = not (LBHOME / "config" / "plugins" / PNAME).is_dir()
PDATA = LBHOME / "data" / "plugins" / PNAME
PLOG = LBHOME / "log" / "plugins" / PNAME
PCONFIG = LBHOME / "config" / "plugins" / PNAME

DATEI_CONFIG = PCONFIG / "vw.json"
# Die Zweitschrift neben dem Konfigordner - der Dienst LIEST sie, wenn vw.json
# fehlt, leer oder beschaedigt ist, genau wie vw_config_lesen() in der
# Oberflaeche und am Endpunkt (C7, Durchgang 02.10.2026). Geschrieben wird sie
# hier nie.
DATEI_ZWEITSCHRIFT = LBHOME / "config" / "plugins" / (PNAME + ".backup.vw.json")
DATEI_ZUGANG = PCONFIG / "zugang.json"
DATEI_CACHE = PDATA / "cache.json"
DATEI_LOXONE = PDATA / "loxone.json"
DATEI_ZUSTAND = PDATA / "zustand.json"
DATEI_TOKEN = PDATA / "token.json"          # Anmeldemarken der Bibliothek
DATEI_ZWISCHEN = PDATA / "bibliothek_cache.json"
# Merker des einmaligen Abraeumens zurueckbehaltener Dienstaussagen (0.9.24).
# Er liegt IM Datenordner: purge_installation raeumt ihn beim Upgrade mit ab,
# und nach einer Neuinstallation ist ohnehin nichts abzuraeumen, was dieses
# Plugin hinterlassen haette - die Deinstallation leert den Broker.
DATEI_RETAIN_GERAEUMT = PDATA / "retain_dienstzustand_geraeumt"
# Praefixe, unter denen noch zurueckbehaltene Werte dieses Plugins liegen
# koennen (M6, Durchgang 02.10.2026): das zuletzt retained beschickte und die
# zum Abraeumen vorgemerkten. --mqtt-leeren (Deinstallation) liest sie mit.
DATEI_MQTT_PRAEFIXE = PDATA / "mqtt_praefixe.json"
ORDNER_BEFEHLE = PDATA / "befehle"
ORDNER_ANTWORTEN = PDATA / "antworten"
DATEI_LOG = PLOG / "vw.log"

# Die Zuordnung Fahrgestellnummer -> Fahrzeugnummer. Sie liegt NEBEN dem
# Datenordner, nicht darin: plugininstall.pl raeumt data/plugins/<ordner>/
# beim Upgrade vollstaendig ab (purge_installation, Aufrufstelle :886 im
# Upgrade-Zweig), eine Datei daneben ueberlebt. uninstall/uninstall raeumt
# sie weg.
DATEI_NUMMERN = LBHOME / "data" / "plugins" / (PNAME + ".nummern.json")

# Muessen zu vw_vorgaben() in webfrontend/html/vw_lib.php passen.
#
# Der Takt hat eine harte Untergrenze von 180 Sekunden: der Connector wirft
# darunter beim Anlegen einen ValueError ("Intervall must be at least 180
# seconds"). Der Wert wird deshalb schon hier gekappt, nicht erst dort.
#
# 'aktionstoken', 'wartezeit' und 'wartezeit_endpunkt' fehlen hier mit Absicht:
# sie gehen nur die Oberflaeche und den Endpunkt an. vw_pruefen.py zaehlt die
# gemeinsamen Schluessel nach.
VORGABEN = {
    "intervall": 300,
    "takt_wartung": 12,
    "mqtt_ein": 0,
    "mqtt_topic": "volkswagen",
    "mqtt_retain": 1,
    "steuerung_ein": 0,
    "eingreifend_ein": 0,
    "temp_min": 16,
    "temp_max": 29,
    "verlauf_tage": 8,
    "zugriff_erzwingen": 0,
    "heim_breite": "",
    "heim_laenge": "",
    "heim_radius": 150,
    "abstand_abruf": 120,
    "befehle_stunde": 30,
    "entprellung": 20,
    "empf_thema": "",
    "empf_grenze": "",
    "empf_kleiner": 1,
    "abfahrt_ein": 0,
    "abfahrt_thema": "",
    "abfahrt_vorlauf": 20,
    "abfahrt_temp": 21,
    # Nr. 36 b (seit 0.9.29): Ansageanlaesse - dieselben Schluessel und Werte wie in
    # vw_vorgaben(). Der Block tts gehoert der Bruecke (bin/vw_ansage.php).
    "ansage_laden_fertig": 1,
    "ansage_laden_abbruch": 1,
    "ansage_offen": 1,
    "ansage_licht": 1,
    "ansage_klima": 1,
    "ansage_ausfall": 1,
    # --- Gruppe 41 (0.9.33): Hausform - dieselben Werte wie in vw_vorgaben(), alles ab Werk aus ---
    "befehle_mqtt_ein": 0,
    "befehle_mqtt_steuern": 0,
    "endpunkt_ein": 0,
    # B5: Fahrzeug fuer die Vorklimatisierung (bis 0.9.32 die kleinste feste Nummer im Konto).
    "abfahrt_fahrzeug": 1,
    # Haus-Themen (Regeln/07 Nr. 15) ueber die Dauerverbindung des Horchers.
    "haus_abfahrt_ein": 0,
    "vorklima_anwesend_ein": 0,
    "vorklima_wetter_ein": 0,
    "vorklima_kalt_unter": 5,
    "vorklima_heiss_ueber": 25,
    "ankunft_ein": 0,
    "ankunft_quelle": "",
    "haus_strompreis_ein": 0,
    # Zweite Quelle der Ladeempfehlung: "thema" (wie bisher) oder "haus" (haus/strompreis/aktuell).
    "ladeempf_quelle": "thema",
    # Meldungen an SignalBot (E9): Hauptschalter aus, je Ereignis abwaehlbar.
    "signal_ein": 0,
    "signal_praefix": "signalbot",
    "signal_laden_fertig": 1,
    "signal_laden_abbruch": 1,
    "signal_offen": 1,
    "signal_ausfall": 1,
    "signal_vorklima": 1,
}

# Grenzen je Einstellung - dieselben Zahlen wie in vw_regeln() der Bibliothek.
# Eine Grenze steht an EINER Stelle je Sprache; ueber die Sprachgrenze hinweg
# gibt es keine gemeinsame Funktion, also wird sie hier wiederholt und von
# vw_pruefen.py gegengezaehlt.
GRENZEN_GANZ = {
    "intervall": (180, 3600),
    "takt_wartung": (1, 240),
    "temp_min": (10, 30),
    "temp_max": (10, 30),
    "verlauf_tage": (1, 90),
    "heim_radius": (10, 5000),
    "abstand_abruf": (0, 3600),
    "befehle_stunde": (1, 240),
    "entprellung": (0, 600),
    "abfahrt_vorlauf": (5, 180),
    "abfahrt_temp": (10, 30),
    # Gruppe 41 (0.9.33)
    "abfahrt_fahrzeug": (1, 99),
    "vorklima_kalt_unter": (-30, 45),
    "vorklima_heiss_ueber": (-30, 45),
}
GRENZEN_SCHALT = ("mqtt_ein", "mqtt_retain", "steuerung_ein", "eingreifend_ein",
                  "zugriff_erzwingen", "empf_kleiner", "abfahrt_ein",
                  "ansage_laden_fertig", "ansage_laden_abbruch", "ansage_offen",
                  "ansage_licht", "ansage_klima", "ansage_ausfall",
                  # Gruppe 41 (0.9.33)
                  "befehle_mqtt_ein", "befehle_mqtt_steuern", "endpunkt_ein", "haus_abfahrt_ein",
                  "vorklima_anwesend_ein", "vorklima_wetter_ein", "ankunft_ein", "haus_strompreis_ein",
                  "signal_ein", "signal_laden_fertig", "signal_laden_abbruch", "signal_offen",
                  "signal_ausfall", "signal_vorklima")
# Kommazahlen und Themen - dieselben Regeln wie in vw_regeln() (C7).
GRENZEN_ZAHL = {
    "heim_breite": (-90, 90),
    "heim_laenge": (-180, 180),
    "empf_grenze": (-100000, 100000),
}
MUSTER_TEXT = {
    "mqtt_topic": (r"[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*", 64),
    "empf_thema": (r"[A-Za-z0-9_\-/]*", 128),
    "abfahrt_thema": (r"[A-Za-z0-9_\-/]*", 128),
    # Gruppe 41 (0.9.33) - dieselben Muster wie vw_regeln()
    "ankunft_quelle": (r"((tag|person):[A-Za-z0-9_-]{1,40}(,(tag|person):[A-Za-z0-9_-]{1,40}){0,3})?", 200),
    "ladeempf_quelle": (r"thema|haus", 5),
    "signal_praefix": (r"[A-Za-z0-9_-]{1,40}", 40),
}

TAKT_MIN = 180

# Zustaende, die Loxone als Zahl braucht. Ein unbekannter Zustand wird zu None
# (am Endpunkt ein Strich), NICHT zu 0 - eine 0 waere eine stille
# Falschaussage: "Tueren zu", obwohl niemand es weiss.
OFFEN_ZU = {"closed": 0, "open": 1, "ajar": 1}
AN_AUS = {"off": 0, "on": 1}
VERRIEGELT = {"locked": 1, "unlocked": 0}
KLIMA_AN = {"off": 0, "heating": 1, "cooling": 1, "ventilation": 1}
LADEN_AN = {"charging": 1, "off": 0, "ready_for_charging": 0, "conservation": 0,
            "error": 0, "discharging": 0}
KABEL = {"connected": 1, "disconnected": 0}
STECKER = {"locked": 1, "unlocked": 0}
# Fahrzeugzustand als Stufe: je hoeher, desto "wacher".
FAHRZEUGZUSTAND = {"offline": 0, "parked": 1, "ignition_on": 2, "driving": 3}
ERREICHBAR = {"online": 1, "reachable": 1, "connected": 1,
              "offline": 0, "disconnected": 0, "error": 0}

# Ladestrom: der Connector nimmt nur diese Stufen an. Fahrzeuge, die den Strom
# in Ampere fuehren, koennen 5/10/13/32; die uebrigen kennen nur "reduziert"
# (6) und "maximal" (16). Welches von beidem, weiss nur das Fahrzeug - deshalb
# werden hier alle sechs zugelassen und die Ablehnung der Bibliothek
# weitergereicht, statt vorher zu raten.
LADESTROM_STUFEN = (5, 6, 10, 13, 16, 32)

# Befehle, die das Fahrzeug oeffnen oder auffindbar machen. Sie haengen an
# einem eigenen Haken, der ab Werk aus ist - muss zu vw_befehle() in
# webfrontend/html/vw_lib.php passen.
EINGREIFEND = ("verriegeln", "entriegeln", "blinken", "hupen")

_LAUF = True
_LOG = logging.getLogger("volkswagen")
# Vor log_einrichten() (--selbsttest, --mqtt-leeren) geht keine Zeile auf
# stderr: config() meldet seit dem Durchgang 02.10.2026 abgewiesene Werte.
_LOG.addHandler(logging.NullHandler())
_LETZTE_MELDUNG: dict[str, float] = {}


class WachsameRotation(RotatingFileHandler):
    """Umlaufender Protokollhandler, der eine geloeschte Datei neu oeffnet.

    `log/plugins` liegt auf einer Ramdisk (zram). Wird sie geleert, raeumt
    LoxBerrys `log_maint` auf, oder loescht jemand die Datei von Hand, dann
    schreibt ein einmal geoeffneter Handler bis zum Prozessende in einen
    Inode, den es nicht mehr gibt - ohne Fehlermeldung, ohne Datei, ohne
    Hinweis. Am Geraet gemessen (06.09.2026, Python 3.13.5): FileHandler und
    RotatingFileHandler verlieren die Zeile, WatchedFileHandler nicht.

    Die Standardbibliothek hat den WatchedFileHandler, aber nicht zusammen
    mit dem Umlauf. Deshalb hier beides: vor jeder Zeile Geraetenummer und
    Inode vergleichen, bei Abweichung neu oeffnen, nach jedem Umlauf die
    Kennung nachfuehren.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._kennung = self._kennung_lesen()

    def _kennung_lesen(self):
        """(Geraetenummer, Inode) der Datei - None, wenn es sie nicht gibt."""
        try:
            s = os.stat(self.baseFilename)
        except OSError:
            return None
        return (s.st_dev, s.st_ino)

    def _nachfassen(self):
        """Neu oeffnen, wenn unter dem offenen Deskriptor eine andere (oder
        gar keine) Datei mehr liegt."""
        if self._kennung_lesen() == self._kennung:
            return
        if self.stream is not None:
            try:
                self.stream.flush()
            finally:
                self.stream.close()
                self.stream = None
        self.stream = self._open()
        self._kennung = self._kennung_lesen()

    def emit(self, record):
        try:
            self._nachfassen()
        except Exception:
            # Ein Fehlschlag beim Nachfassen darf die Zeile nicht kosten:
            # lieber in den alten Deskriptor schreiben als gar nicht.
            pass
        super().emit(record)

    def doRollover(self):
        super().doRollover()
        self._kennung = self._kennung_lesen()


# ---------------------------------------------------------------------------
# Protokollierung
#
# Ausschliesslich in die Datei. Das Startskript leitet die Ausgabe des Dienstes
# ohnehin in dieselbe Datei um - ein zweiter Kanal nach stdout schriebe jede
# Zeile doppelt hinein.
# ---------------------------------------------------------------------------
def log_einrichten() -> None:
    # Der mkdir gehoert in dasselbe try wie der Handler. Bis 0.9.9 stand er
    # davor und ausserhalb jeder Absicherung: ein OSError - Ramdisk voll,
    # Rechte falsch - beendete den Dienst, bevor die erste Zeile geschrieben
    # war, und weil der Sollmerker liegenblieb, startete der Waechter ihn im
    # Minutentakt neu.
    try:
        PLOG.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    _LOG.setLevel(logging.INFO)
    try:
        h = WachsameRotation(DATEI_LOG, maxBytes=512000, backupCount=1, encoding="utf-8")
    except OSError as err:
        h = logging.StreamHandler(sys.stderr)
        print(f"Logdatei nicht beschreibbar ({err}) - schreibe nach stderr.", file=sys.stderr)
    h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    _LOG.handlers = [h]
    _LOG.propagate = False
    # Die Bibliothek protokolliert reichlich und in den eigenen Wurzel-Logger.
    # Ohne diesen Umweg landet nichts davon in der Plugin-Logdatei, und mit
    # DEBUG wuerde sie unlesbar.
    wurzel = logging.getLogger("carconnectivity")
    wurzel.handlers = [h]
    wurzel.setLevel(logging.WARNING)
    wurzel.propagate = False
    for fremd in ("requests", "urllib3", "oauthlib", "requests_oauthlib"):
        logging.getLogger(fremd).setLevel(logging.WARNING)


def melde_gebremst(schluessel: str, text: str, sekunden: int = 3600) -> None:
    """Dieselbe Meldung hoechstens einmal je Zeitfenster - sonst wird die
    Logdatei durch eine Dauerstoerung unlesbar."""
    jetzt = time.time()
    if jetzt - _LETZTE_MELDUNG.get(schluessel, 0) >= sekunden:
        _LETZTE_MELDUNG[schluessel] = jetzt
        _LOG.warning(text)


# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------
def json_lesen(pfad: Path) -> dict:
    try:
        with pfad.open("r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def nebendatei_schreiben(pfad: Path, schreiber, rechte: int | None = None) -> None:
    """Erst in eine Nebendatei <datei>.tmp.<pid>, dann umbenennen.

    Seit dem Durchgang 02.10.2026 (Baubericht, Frage 6): die Nebendatei traegt
    die Prozessnummer - zwei Prozesse schreiben nie in dieselbe -, und die
    Rechte stehen VOR dem Inhalt (Hausregel "Rechte vor Inhalt"). Bis 0.9.26
    hiess sie fuer alle Prozesse <datei>.tmp und bekam die Rechte erst nach dem
    Schreiben: loxone.json und cache.json mit Position und VIN lagen dazwischen
    mit den Rechten der umask (0644) auf der Karte. Nach dem Schreiben folgt
    fsync, damit ein Stromausfall nach dem rename keine leere Datei hinterlaesst.
    Scheitert etwas, wird die Nebendatei weggeraeumt und der Fehler weitergereicht.
    Ohne rechte gelten wie bisher die der umask."""
    tmp = pfad.with_name("%s.tmp.%d" % (pfad.name, os.getpid()))
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 0o600 if rechte is not None else 0o666)
    try:
        if rechte is not None:
            # Eine liegengebliebene Nebendatei derselben Nummer behielte bei
            # O_TRUNC ihre alten Rechte - deshalb ausdruecklich setzen.
            os.fchmod(fd, rechte)
        f = os.fdopen(fd, "w", encoding="utf-8")
        fd = -1
        with f:
            schreiber(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, pfad)
    except BaseException:
        if fd >= 0:
            os.close(fd)
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def json_schreiben(pfad: Path, daten, rechte: int | None = None) -> bool:
    """Erst in eine Nebendatei, dann umbenennen. So liest die Oberflaeche nie
    eine halb geschriebene Datei (nebendatei_schreiben)."""
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        nebendatei_schreiben(
            pfad, lambda f: json.dump(daten, f, ensure_ascii=False, indent=1, default=str),
            rechte)
        return True
    except (OSError, TypeError, ValueError) as err:
        _LOG.error("Datei %s konnte nicht geschrieben werden: %s", pfad, err)
        return False


def ganz(wert, ersatz: int) -> int:
    try:
        return int(wert)
    except (TypeError, ValueError):
        return ersatz


def schalt(wert, ersatz: int = 0) -> int:
    """Einen Ja/Nein-Wert deuten - und zwar streng.

    Bis 0.9.9 wurde 'steuerung_ein' gar nicht umgewandelt und mit
    `if not cfg.get(...)` geprueft. bool("0") ist in Python WAHR, empty("0") in
    PHP dagegen wahr im Sinne von leer: eine Zeichenkette "0" in vw.json - etwa
    aus einer zurueckgespielten Sicherung - liess den Dienst schalten, waehrend
    Oberflaeche und Endpunkt "gesperrt" anzeigten. Gemessen am 27.08.2026.

    Deshalb: nur 0/1, "0"/"1", True/False gelten. Alles andere ist der
    Ersatzwert - und bei einem Sicherheitsschalter ist das die geschlossene
    Stellung.
    """
    if isinstance(wert, bool):
        return 1 if wert else 0
    if isinstance(wert, int):
        return 1 if wert == 1 else (0 if wert == 0 else ersatz)
    if isinstance(wert, str):
        s = wert.strip()
        if s == "1":
            return 1
        if s == "0":
            return 0
    return ersatz


def komma(wert, ersatz=None):
    """Eine Kommazahl, oder der Ersatz. Leer bleibt leer, nicht 0."""
    if wert is None or wert == "":
        return ersatz
    try:
        return float(str(wert).replace(",", "."))
    except (TypeError, ValueError):
        return ersatz


def _als_text(wert):
    """Ein Wert als Text - so, wie vw_wert_taugt() und (string) in PHP ihn sehen.

    None heisst: das ist gar kein Wert (Liste, Objekt, Wahrheitswert, Null,
    Steuerzeichen, mehr als 4096 Zeichen). Eine ganze Kommazahl wird ohne
    Nachkommastelle geschrieben, wie PHP es tut ((string)300.0 ist "300").
    """
    if wert is None or isinstance(wert, (bool, list, dict)):
        return None
    if isinstance(wert, float):
        if wert != wert or wert in (float("inf"), float("-inf")):
            return None
        s = str(int(wert)) if wert.is_integer() else repr(wert)
    else:
        s = str(wert)
    if len(s) > 4096 or re.search(r"[\x00-\x08\x0A-\x1F\x7F]", s):
        return None
    return s.strip()


def _wert_pruefen(name: str, wert) -> tuple:
    """(gueltig, Wert) fuer EINE Einstellung - dieselbe Regel wie
    vw_wert_pruefen() in webfrontend/html/vw_lib.php (C7, Durchgang 02.10.2026).

    Bis 0.9.26 kappte der Dienst einen Wert ausserhalb der Grenzen still auf
    die Grenze (intervall 99999 -> 3600), waehrend die Oberflaeche ihn abwies
    und die Vorgabe zeigte (intervall 300): Dienst und Oberflaeche arbeiteten
    mit verschiedenen Einstellungen (gemessen, Codebericht Nr. 9a). Jetzt gilt
    an beiden Enden dieselbe Antwort: abweisen, Vorgabe, Protokollzeile.
    """
    if name in GRENZEN_GANZ:
        s = _als_text(wert)
        if s is None or not re.fullmatch(r"-?[0-9]+", s):
            return (False, None)
        n = int(s)
        lo, hi = GRENZEN_GANZ[name]
        return (lo <= n <= hi, n)
    if name in GRENZEN_SCHALT:
        s = _als_text(wert)
        return (True, int(s)) if s in ("0", "1") else (False, None)
    if name in MUSTER_TEXT:
        s = _als_text(wert)
        muster, laenge = MUSTER_TEXT[name]
        if s is None or len(s) > laenge or not re.fullmatch(muster, s):
            return (False, None)
        return (True, s)
    if name in GRENZEN_ZAHL:
        if isinstance(wert, (int, float)) and not isinstance(wert, bool):
            f = float(wert)
        else:
            s = _als_text(wert)
            if s is None:
                return (False, None)
            if s == "":
                return (True, "")
            if not re.fullmatch(r"-?[0-9]+([.,][0-9]+)?", s):
                return (False, None)
            f = float(s.replace(",", "."))
        lo, hi = GRENZEN_ZAHL[name]
        return (lo <= f <= hi, f) if f == f else (False, None)
    return (True, wert)


def _config_roh() -> tuple:
    """(Werte, Lage) der Konfigurationsdatei, gelesen wie vw_config_lesen().

    Lage: ok | leer | kaputt | aus_zweitschrift. Fehlt vw.json, ist sie leer,
    "{}" oder kein JSON-Objekt, wird die Zweitschrift neben dem Konfigordner
    GELESEN (nicht kopiert). Bis 0.9.26 nahm der Dienst in diesem Fall still
    die Vorgaben - bei abgeschnittener vw.json fuhr er mit Steuerung aus und
    MQTT aus, waehrend Endpunkt und Oberflaeche aus der Zweitschrift Steuerung
    an zeigten (gemessen, Codebericht Nr. 9b).
    """
    try:
        roh = DATEI_CONFIG.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        roh = ""
    lage = "ok"
    werte = None
    if roh in ("", "{}"):
        lage = "leer"
    else:
        try:
            werte = json.loads(roh)
        except ValueError:
            werte = None
        if not isinstance(werte, dict):
            werte = None
            lage = "kaputt"
    if werte is not None:
        return werte, lage
    try:
        s = json.loads(DATEI_ZWEITSCHRIFT.read_text(encoding="utf-8").strip() or "null")
    except (OSError, ValueError):
        s = None
    if isinstance(s, dict) and s:
        return s, "aus_zweitschrift"
    return {}, lage


def config() -> dict:
    """Die Konfiguration - vollstaendig und geprueft.

    Jeder Wert wird gegen dieselben Regeln gehalten, die auch das Formular und
    das Zurueckspielen benutzen (vw_regeln() in der Oberflaeche). Eine Datei
    kann von Hand geschrieben, aus einer Sicherung zurueckgespielt oder aus
    einer aelteren Fassung uebernommen sein; geprueft wird deshalb an BEIDEN
    Enden, nicht nur beim Speichern - und an beiden Enden mit derselben
    Antwort (C7, Durchgang 02.10.2026): ein unzulaessiger Wert wird nicht
    gekappt, sondern abgewiesen; es gilt die Vorgabe, und das Protokoll sagt
    es (hoechstens einmal je Stunde und Schluessel).
    """
    roh, lage = _config_roh()
    c = dict(VORGABEN)
    abgewiesen = []
    for name, wert in roh.items():
        if name not in VORGABEN:
            continue
        ok, rein = _wert_pruefen(name, wert)
        if ok:
            c[name] = rein
        else:
            abgewiesen.append(name)
    # temp_min > temp_max: beide fuer sich zulaessig, zusammen nicht. Bis
    # 0.9.26 wurden sie still getauscht (Nr. 19); jetzt eine Beanstandung wie
    # in der Oberflaeche - beide fallen auf die Vorgabe.
    if c["temp_min"] > c["temp_max"]:
        abgewiesen += ["temp_min", "temp_max"]
        c["temp_min"], c["temp_max"] = VORGABEN["temp_min"], VORGABEN["temp_max"]
    # Gruppe 41: die Wettergrenze braucht kalt < heiss - sonst beide auf die Vorgabe (wie temp_min/max).
    if c["vorklima_kalt_unter"] >= c["vorklima_heiss_ueber"]:
        abgewiesen += ["vorklima_kalt_unter", "vorklima_heiss_ueber"]
        c["vorklima_kalt_unter"] = VORGABEN["vorklima_kalt_unter"]
        c["vorklima_heiss_ueber"] = VORGABEN["vorklima_heiss_ueber"]
    for name in abgewiesen:
        melde_gebremst("konfig_" + name,
                       "Konfiguration: der Wert von '%s' ist unzulaessig - es gilt die "
                       "Voreinstellung %r. Dieselbe Regel wie in der Oberflaeche; dort steht "
                       "er unter 'unzulaessig'." % (name, VORGABEN[name]), 3600)
    if lage == "aus_zweitschrift":
        melde_gebremst("konfig_lage",
                       "Konfiguration: vw.json fehlte, war leer oder beschaedigt - gelesen "
                       "wurde die Zweitschrift %s. Die Oberflaeche einmal oeffnen; sie legt "
                       "die Datei wieder an." % DATEI_ZWEITSCHRIFT, 3600)
    elif lage == "kaputt":
        melde_gebremst("konfig_lage",
                       "Konfiguration: vw.json ist beschaedigt, und eine brauchbare "
                       "Zweitschrift gibt es nicht - es gelten die Voreinstellungen.", 3600)
    # Heimatort: leer bleibt leer. Eine 0/0 waere ein Punkt im Atlantik, und
    # jede Entfernungsangabe daraus waere eine Zahl, die richtig aussieht.
    for name in ("heim_breite", "heim_laenge", "empf_grenze"):
        if c[name] != "":
            c[name] = float(c[name])
    return c


def zugang() -> dict:
    z = json_lesen(DATEI_ZUGANG)
    return {
        "email": str(z.get("email") or "").strip(),
        "passwort": str(z.get("passwort") or ""),
        "spin": str(z.get("spin") or ""),
    }


# ---------------------------------------------------------------------------
# MQTT ueber das LoxBerry-Gateway
#
# Das MQTT-Gateway ist seit LoxBerry 3 Bestandteil des Systems, kein Plugin.
# Es wird nicht nachinstalliert, sondern unter System -> MQTT Gateway
# eingeschaltet.
#
# Achtung: Mqtt.Brokerhost ist ab Werk gesetzt ("localhost"). Eine Pruefung
# darauf beantwortet also NICHT die Frage, ob Nachrichten ankommen koennen.
# Massgeblich ist Gatewayautostart.
# ---------------------------------------------------------------------------
def mqtt_zustand() -> dict:
    gen = json_lesen(LBHOME / "config" / "system" / "general.json")
    m = gen.get("Mqtt") or gen.get("mqtt") or {}
    autostart = m.get("Gatewayautostart", m.get("gatewayautostart"))
    udp = m.get("Udpinport", m.get("udpinport"))
    try:
        udp = int(udp)
    except (TypeError, ValueError):
        udp = 0
    return {
        "gefunden": bool(m),
        "autostart": 1 if str(autostart) in ("1", "true", "True") else 0,
        "udpport": udp,
        "broker": str(m.get("Brokerhost", m.get("brokerhost", ""))),
        "brokerport": str(m.get("Brokerport", m.get("brokerport", ""))),
        # Der LoxBerry-Broker verlangt ab Werk eine Anmeldung. Bis 0.9.11 las
        # dieses Plugin die beiden Schluessel nicht und verband sich anonym -
        # der Horcher bekam dann nie eine Nachricht. Das schwesterliche
        # Audi-Plugin liest sie seit jeher (bin/audi.py, 'Brokeruser').
        # Nur der HORCHER braucht sie; der Sendeweg geht ueber den
        # UDP-Eingang des Gateways und kennt keine Anmeldung.
        "benutzer": str(m.get("Brokeruser", m.get("brokeruser", ""))),
        "passwort": str(m.get("Brokerpass", m.get("brokerpass", ""))),
    }


def mqtt_ohne_retain(schluessel: str) -> bool:
    """Gehoert dieses Thema zu denen, die NICHT behalten werden?

    Der Schluessel ist entweder 'ts' (oberhalb der Fahrzeugebene) oder
    'fahrzeug1/ladeleistung_kw'. Entschieden wird ueber den Teil hinter dem
    letzten Schraegstrich, damit dieselbe Liste fuer beide Ebenen gilt.
    """
    return schluessel.rsplit("/", 1)[-1] in MQTT_OHNE_RETAIN


def mqtt_senden(paare: dict, praefix: str, retain: int = 1) -> tuple[int, int]:
    """Veroeffentlicht die Paare ueber den UDP-Eingang des Gateways.

    Rueckgabe: (versucht, misslungen). Bis 0.9.9 gab die Funktion nichts
    zurueck und meldete nur gebremst ins Protokoll - eine Zahl in der
    Oberflaeche beantwortet dagegen die Frage, ob ueberhaupt etwas hinausgeht,
    auch dann, wenn das Gateway gar nicht eingerichtet ist.

    'retain' laesst das Gateway die Werte behalten. Ohne das sind nach einem
    Neustart des Brokers alle virtuellen Eingaenge in Loxone ohne Wert, bis der
    naechste Abruf durch ist - bei einem Takt von fuenf Minuten also minutenlang.
    Das Befehlswort 'retain' kennt der UDP-Eingang des Gateways: am LoxBerry
    im Quelltext nachgesehen (sbin/mqttgateway.pl, 06.09.2026) und in der
    Wirkung gemessen (ein so gesendetes Thema lag danach im Broker,
    13./14.09.2026).

    Seit 0.9.12 entscheidet es JE THEMA (siehe MQTT_OHNE_RETAIN): Zustaende
    werden behalten, Messwerte mit Zeitbezug und das Lebenszeichen nicht.
    """
    z = mqtt_zustand()
    if not z["udpport"]:
        melde_gebremst("mqtt_kein_port",
                       "MQTT: kein UDP-Eingangsport in general.json gefunden - nichts gesendet.")
        return (0, 0)
    if not z["autostart"]:
        melde_gebremst(
            "mqtt_aus",
            "MQTT: das Gateway ist nicht auf Autostart gestellt (System -> MQTT Gateway). "
            "Es wird gesendet, aber vermutlich hoert niemand zu.",
        )
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as err:
        melde_gebremst("mqtt_socket", f"MQTT: Socket nicht moeglich ({err}).")
        return (0, 0)
    versucht = 0
    schlecht = 0
    try:
        for k, v in paare.items():
            if v is None:
                continue
            text = mqtt_wert_saeubern(v)
            # Eine LEERE Nutzlast loescht mit 'retain' ein behaltenes Thema.
            # Ein Wert, der nach dem Saeubern nichts uebrig laesst, ist kein
            # Wert - er wird uebersprungen wie None.
            if text == "":
                continue
            versucht += 1
            # Je Thema entschieden, nicht je Durchgang. Steht der Haken
            # "Werte behalten" auf aus, geht ohnehin alles ohne Retain.
            befehl = "retain" if (retain and not mqtt_ohne_retain(k)) else "publish"
            try:
                s.sendto(f"{befehl} {praefix}/{k} {text}".encode("utf-8"),
                         ("127.0.0.1", z["udpport"]))
            except OSError:
                schlecht += 1
            # 5 ms zwischen zwei Datagrammen (M9, Durchgang 02.10.2026;
            # Regeln/07): am Geraet gingen ohne Pause von 90 Datagrammen
            # bis zu 6 verloren, mit 5 ms keines. Bis 0.9.26 lagen 117
            # Datagramme in 30 ms (gemessen, MQTT-Bericht V11). Kein
            # Aenderungsfilter: der Vollversand je Takt ist die Selbstheilung.
            time.sleep(0.005)
    except OSError as err:
        melde_gebremst("mqtt_senden", f"MQTT: Senden fehlgeschlagen ({err}).")
    finally:
        s.close()
    if schlecht:
        melde_gebremst("mqtt_teil",
                       f"MQTT: {schlecht} von {versucht} Meldungen sind nicht hinausgegangen.")
    return (versucht, schlecht)


# ---------------------------------------------------------------------------
# Werte aus den Attributobjekten der Bibliothek holen
#
# Jedes Feld ist ein Attributobjekt mit .value, .unit und .enabled. Ein Feld,
# das das Fahrzeug nicht liefert, ist entweder nicht enabled oder hat den Wert
# None - beides ergibt hier None und am Endpunkt einen Strich. Bewusst keine 0:
# eine 0 waere eine stille Falschaussage.
# ---------------------------------------------------------------------------
def wert(attr, einheit=None, nachkomma: int | None = None):
    """Wert eines Attributs, auf Wunsch in eine feste Einheit umgerechnet.

    Die Einheit ist wichtig: die Bibliothek liefert je nach Konto Kilometer
    oder Meilen, Grad Celsius oder Fahrenheit. Wer das nicht umrechnet, sendet
    irgendwann Meilen an einen Baustein, der Kilometer erwartet - und niemand
    sieht es, weil die Zahl plausibel bleibt.
    """
    if attr is None:
        return None
    try:
        if attr.enabled is False:
            return None
    except AttributeError:
        pass
    v = getattr(attr, "value", None)
    if v is None:
        return None
    if einheit is not None:
        u = getattr(attr, "unit", None)
        if u is not None and u != einheit:
            try:
                v = type(attr).convert(v, u, einheit)
            except Exception:  # noqa: BLE001 - eine misslungene Umrechnung ist kein Wert
                return None
            if v is None:
                return None
    if isinstance(v, bool):
        return 1 if v else 0
    if nachkomma is not None:
        try:
            return round(float(v), nachkomma) if nachkomma else int(round(float(v)))
        except (TypeError, ValueError):
            return None
    return v


def etext(attr) -> str:
    """Ein Aufzaehlungswert als Zeichenkette ('locked', 'charging' ...)."""
    v = getattr(attr, "value", None) if attr is not None else None
    if v is None:
        return ""
    return str(getattr(v, "value", v))


def kennzahl(attr, tabelle: dict):
    """Aufzaehlungswert in eine Zahl fuer Loxone. Unbekannt bleibt None."""
    t = etext(attr).lower()
    return tabelle.get(t)


def tage_bis(attr) -> int | None:
    """Tage bis zu einem Termin. Vergangene Termine ergeben negative Werte -
    das ist gewollt: 'Inspektion seit 12 Tagen faellig' ist eine Aussage."""
    d = getattr(attr, "value", None) if attr is not None else None
    if not isinstance(d, datetime):
        return None
    jetzt = datetime.now(timezone.utc) if d.tzinfo else datetime.now()
    return int(round((d - jetzt).total_seconds() / 86400))


def zeitstempel(attr) -> int | None:
    d = getattr(attr, "value", None) if attr is not None else None
    if not isinstance(d, datetime):
        return None
    try:
        return int(d.timestamp())
    except (OSError, OverflowError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Fehlermeldungen, die sagen, wer geantwortet hat
# ---------------------------------------------------------------------------
def grund_von(err: Exception) -> str:
    """Ein kurzes Kennwort fuer die Statuszeile.

    Der Klartext geht in eine eigene Zeile; die Statuszeile bleibt rein aus
    Zahlen und diesem einen Wort. In Loxone laesst sich darauf eine
    Benachrichtigung legen, die sagt, WER nicht geantwortet hat - 'unerreichbar'
    fuer alles hat einmal vier Messbefehle am Geraet gekostet, nur um zu
    klaeren, welcher Weg scheiterte.
    """
    name = type(err).__name__
    klein = (str(err) or name).lower()
    if name == "TooManyRequestsError":
        return "KONTINGENT"
    if name in ("AuthenticationError", "TemporaryAuthenticationError"):
        return "ANMELDUNG"
    if name == "APICompatibilityError":
        return "SCHNITTSTELLE"
    if name == "ConfigurationError":
        return "EINSTELLUNG"
    if name in ("SetterError", "CommandError"):
        return "BEFEHL"
    if name == "TimeoutError" or "timed out" in klein or name in ("ReadTimeout", "ConnectTimeout"):
        return "ZEITUEBERLAUF"
    grund = getattr(err, "os_error", None)
    errno = getattr(grund, "errno", None) if grund is not None else getattr(err, "errno", None)
    if errno in (111, 113, -2, -3):
        return "NETZ"
    if "<html" in klein or "<!doctype" in klein:
        return "VORGELAGERT"
    if name in ("RetrievalError", "MultipleRetrievalError", "APIError"):
        return "ABRUF"
    return "UNBEKANNT"


def fehlertext(err: Exception) -> str:
    name = type(err).__name__
    inhalt = str(err) or name
    klein = inhalt.lower()

    if name in ("TooManyRequestsError",):
        return ("Volkswagen hat wegen zu vieler Anfragen abgewiesen. Den Takt in den "
                "Einstellungen vergroessern; unter 300 Sekunden ist erfahrungsgemaess zu dicht.")
    if name in ("TemporaryAuthenticationError",):
        return ("Die Anmeldung war voruebergehend nicht moeglich. Meist eine Stoerung bei "
                "Volkswagen - der naechste Takt versucht es erneut.")
    if name in ("AuthenticationError",):
        if "netrc" in klein:
            return ("Die Zugangsdatei konnte nicht gelesen werden. Zugangsdaten in der "
                    "Oberflaeche neu eintragen und speichern.")
        return ("Anmeldung abgewiesen: Benutzername oder Passwort stimmen nicht. Es sind die "
                "Zugangsdaten des Volkswagen-Kontos aus der App 'Volkswagen' beziehungsweise "
                "'We Connect ID', nicht die eines Haendlerportals. Wenn beides stimmt: Volkswagen "
                "verlangt bei manchen Konten eine Zwei-Faktor-Bestaetigung, die sich hier nicht "
                "automatisieren laesst - dann einmal im Browser auf diesem Geraet anmelden.")
    if name in ("APICompatibilityError",):
        return ("Die Antwort von Volkswagen sah anders aus als erwartet. Das passiert, wenn "
                "Volkswagen die Schnittstelle umbaut - dann hilft nur eine neuere Fassung von "
                "carconnectivity.")
    if name in ("ConfigurationError",):
        return f"Die Bibliothek hat die Einstellungen abgelehnt: {inhalt}"
    if name in ("SetterError", "CommandError"):
        return f"Der Befehl wurde nicht angenommen: {inhalt}"
    if name in ("RetrievalError", "MultipleRetrievalError", "APIError"):
        return f"Abruf fehlgeschlagen: {inhalt}"

    grund = getattr(err, "os_error", None)
    errno = getattr(grund, "errno", None) if grund is not None else getattr(err, "errno", None)
    if errno == 111:
        return ("Verbindung abgewiesen (ECONNREFUSED): der Gegenstelle ist der Port bekannt, "
                "aber es lauscht nichts.")
    if errno == 113:
        return "Kein Weg zum Ziel (EHOSTUNREACH): Netzwerk und Standardroute des LoxBerry pruefen."
    if errno in (-2, -3):
        return ("Namensaufloesung fehlgeschlagen: der DNS-Server des LoxBerry antwortet nicht. "
                "Ohne DNS erreicht das Plugin weder Volkswagen noch den Zeitserver.")
    if "timed out" in klein or name in ("Timeout", "ReadTimeout", "ConnectTimeout"):
        return ("Zeitueberlauf: Volkswagen hat nicht geantwortet. Meist eine gestoerte "
                "Internetverbindung oder eine Stoerung beim Anbieter.")
    if "<html" in klein or "<!doctype" in klein:
        return ("Es kam HTML statt JSON zurueck - geantwortet hat also ein vorgelagerter Dienst "
                "(Proxy, Portal, Fehlerseite), nicht die Volkswagen-Schnittstelle. Die Anmeldung "
                "selbst ist damit nicht der Fehler.")
    return f"{name}: {inhalt}"


# ---------------------------------------------------------------------------
# Abbilden eines Fahrzeugs
#
# Jeder Abschnitt einzeln abgesichert: hat ein Fahrzeug keine Klimatisierung
# oder keinen Ladeanschluss, bleiben genau diese Felder leer.
# ---------------------------------------------------------------------------
def antriebe(fahrzeug) -> dict:
    """Die Antriebe eines Fahrzeugs nach Art sortiert.

    Ein ID.3 hat einen Antrieb, ein GTE deren zwei. Die Bibliothek fuehrt sie
    in einem Verzeichnis, dessen Schluessel je Fahrzeug anders heissen -
    massgeblich ist deshalb die Klasse, nicht der Schluessel.
    """
    elektro = None
    verbrenner = None
    drives = getattr(getattr(fahrzeug, "drives", None), "drives", None) or {}
    for d in drives.values():
        name = type(d).__name__
        if "Electric" in name and elektro is None:
            elektro = d
        elif "Combustion" in name or "Diesel" in name:
            if verbrenner is None:
                verbrenner = d
    return {"elektro": elektro, "verbrenner": verbrenner, "anzahl": len(drives)}


def offene_teile(behaelter, verzeichnis: str) -> tuple:
    """Zaehlt die offenen Einzelteile und nennt sie beim Namen.

    Der Kern fuehrt neben dem Sammelzustand ein Verzeichnis der Einzelteile,
    jedes mit eigenem open_state (Tueren zusaetzlich mit lock_state) -
    gemessen an carconnectivity 0.11.10, doors.py:55 ff., windows.py.

    Ob der Volkswagen-Connector das Verzeichnis fuellt, ist UNGEMESSEN. Ist es
    leer, kommt (None, "") zurueck - kein Wert, keine 0: eine 0 hiesse
    "alles zu", und das weiss hier niemand.
    """
    d = getattr(behaelter, verzeichnis, None) or {}
    if not isinstance(d, dict) or not d:
        return (None, "")
    offen = []
    for name, teil in d.items():
        z = etext(getattr(teil, "open_state", None)).lower()
        if z in ("open", "ajar"):
            offen.append(str(name))
    return (len(offen), ", ".join(sorted(offen)))


def geaendert_vor_min(attr):
    """Minuten seit der letzten AENDERUNG dieses Attributs.

    last_changed, nicht last_updated: das zweite springt bei jedem Abruf, auch
    wenn sich nichts geruehrt hat, und ergaebe eine Standzeit, die immer null
    ist.
    """
    d = getattr(attr, "last_changed", None) if attr is not None else None
    if not isinstance(d, datetime):
        return None
    jetzt = datetime.now(timezone.utc) if d.tzinfo else datetime.now()
    m = int((jetzt - d).total_seconds() / 60)
    return m if m >= 0 else None


def entfernung_m(b1, l1, b2, l2):
    """Entfernung zweier Punkte in Metern (Haversine, Erdradius 6371000 m).

    Dieselbe Formel steht als vw_entfernung_m() in vw_lib.php - ueber die
    Sprachgrenze hinweg gibt es keine gemeinsame Funktion. Die Selbstpruefung
    haelt beide gegeneinander.
    """
    if None in (b1, l1, b2, l2):
        return None
    try:
        import math
        r = 6371000.0
        p1, p2 = math.radians(float(b1)), math.radians(float(b2))
        dp = math.radians(float(b2) - float(b1))
        dl = math.radians(float(l2) - float(l1))
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return int(round(r * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1 - a)))))
    except (TypeError, ValueError):
        return None


def abbild_stamm(fahrzeug, cfg: dict) -> dict:
    from carconnectivity.units import Length
    return {
        "vin": str(wert(getattr(fahrzeug, "vin", None)) or ""),
        "name": str(wert(getattr(fahrzeug, "name", None)) or ""),
        "modell": str(wert(getattr(fahrzeug, "model", None)) or ""),
        "hersteller": str(wert(getattr(fahrzeug, "manufacturer", None)) or ""),
        "baujahr": wert(getattr(fahrzeug, "model_year", None)),
        "kennzeichen": str(wert(getattr(fahrzeug, "license_plate", None)) or ""),
        "antriebsart": etext(getattr(fahrzeug, "type", None)),
        "software": str(wert(getattr(getattr(fahrzeug, "software", None), "version", None)) or ""),
        "kilometerstand": wert(getattr(fahrzeug, "odometer", None), Length.KM, 0),
    }


def abbild_status(fahrzeug, cfg: dict) -> dict:
    from carconnectivity.units import Temperature
    tueren = getattr(fahrzeug, "doors", None)
    fenster = getattr(fahrzeug, "windows", None)
    klima = getattr(fahrzeug, "climatization", None)
    einst = getattr(klima, "settings", None)
    tz, tn = offene_teile(tueren, "doors")
    fz, fn = offene_teile(fenster, "windows")
    return {
        "zustand": kennzahl(getattr(fahrzeug, "state", None), FAHRZEUGZUSTAND),
        "zustand_text": etext(getattr(fahrzeug, "state", None)),
        "erreichbar": kennzahl(getattr(fahrzeug, "connection_state", None), ERREICHBAR),
        "verriegelt": kennzahl(getattr(tueren, "lock_state", None), VERRIEGELT),
        "tueren_offen": kennzahl(getattr(tueren, "open_state", None), OFFEN_ZU),
        "fenster_offen": kennzahl(getattr(fenster, "open_state", None), OFFEN_ZU),
        "licht_an": kennzahl(getattr(getattr(fahrzeug, "lights", None), "light_state", None), AN_AUS),
        "handbremse": wert(getattr(fahrzeug, "parking_brake", None)),
        "aussentemperatur": wert(getattr(fahrzeug, "outside_temperature", None), Temperature.C, 1),
        "klima_an": kennzahl(getattr(klima, "state", None), KLIMA_AN),
        "klima_text": etext(getattr(klima, "state", None)),
        "zieltemperatur": wert(getattr(einst, "target_temperature", None), Temperature.C, 1),
        "klima_fertig_um": zeitstempel(getattr(klima, "estimated_date_reached", None)),
        "scheibenheizung": kennzahl(
            getattr(getattr(fahrzeug, "window_heatings", None), "heating_state", None), AN_AUS),
        "sitzheizung_ein": wert(getattr(einst, "seat_heating", None)),
        "klima_bei_entriegeln": wert(getattr(einst, "climatization_at_unlock", None)),
        "klima_ohne_netz": wert(getattr(einst, "climatization_without_external_power", None)),
        # ---- ab 0.9.10 ----
        "tueren_zahl": tz,
        "tueren_namen": tn,
        "fenster_zahl": fz,
        "fenster_namen": fn,
        "getriebe": etext(getattr(fahrzeug, "gearbox", None)),
        "standzeit_min": geaendert_vor_min(getattr(fahrzeug, "state", None)),
    }


def abbild_reichweite(fahrzeug, cfg: dict) -> dict:
    from carconnectivity.units import Length, Level, Energy, Temperature
    a = antriebe(fahrzeug)
    e, v = a["elektro"], a["verbrenner"]
    batt = getattr(e, "battery", None)
    return {
        "reichweite_km": wert(getattr(getattr(fahrzeug, "drives", None), "total_range", None),
                              Length.KM, 0),
        "anzahl_antriebe": a["anzahl"],
        "soc": wert(getattr(e, "level", None), Level.PERCENTAGE, 0),
        "reichweite_elektro_km": wert(getattr(e, "range", None), Length.KM, 0),
        "batterie_kwh": wert(getattr(batt, "total_capacity", None), Energy.KWH, 1),
        "tank_prozent": wert(getattr(v, "level", None), Level.PERCENTAGE, 0),
        "reichweite_verbrenner_km": wert(getattr(v, "range", None), Length.KM, 0),
        "oelstand_prozent": wert(getattr(v, "oil_level", None), Level.PERCENTAGE, 0),
        # ---- ab 0.9.10 ----
        "batterie_temp": wert(getattr(batt, "temperature", None), Temperature.C, 1),
        "batterie_nutzbar_kwh": wert(getattr(batt, "available_capacity", None), Energy.KWH, 1),
        "reichweite_wltp_km": wert(getattr(e, "range_wltp", None), Length.KM, 0),
        "reichweite_voll_km": wert(getattr(e, "range_estimated_full", None), Length.KM, 0),
        # Der Verbrauch der Bibliothek. Wo ihn das Fahrzeug nicht liefert,
        # rechnet fahrverbrauch_fortschreiben() einen eigenen - und zwar in ein
        # ANDERES Feld, damit gemessen und gerechnet nicht verwechselt werden.
        "verbrauch_fahrzeug": wert(getattr(e, "consumption", None), None, 1),
        "adblue_km": wert(getattr(v, "adblue_range", None), Length.KM, 0),
        "adblue_prozent": wert(getattr(v, "adblue_level", None), Level.PERCENTAGE, 0),
    }


def abbild_laden(fahrzeug, cfg: dict) -> dict:
    from carconnectivity.units import Power, Speed, Level, Current
    laden = getattr(fahrzeug, "charging", None)
    einst = getattr(laden, "settings", None)
    stecker = getattr(laden, "connector", None)
    saeule = getattr(laden, "charging_station", None)
    return {
        "laedt": kennzahl(getattr(laden, "state", None), LADEN_AN),
        "ladezustand_text": etext(getattr(laden, "state", None)),
        "ladeleistung_kw": wert(getattr(laden, "power", None), Power.KW, 1),
        "ladetempo_kmh": wert(getattr(laden, "rate", None), Speed.KMH, 1),
        "ladeart": etext(getattr(laden, "type", None)),
        "laden_fertig_um": zeitstempel(getattr(laden, "estimated_date_reached", None)),
        "ladegrenze": wert(getattr(einst, "target_level", None), Level.PERCENTAGE, 0),
        "ladestrom_a": wert(getattr(einst, "maximum_current", None), Current.A, 0),
        "stecker_entriegeln": wert(getattr(einst, "auto_unlock", None)),
        "kabel_verbunden": kennzahl(getattr(stecker, "connection_state", None), KABEL),
        "stecker_verriegelt": kennzahl(getattr(stecker, "lock_state", None), STECKER),
        "externe_stromversorgung": etext(getattr(stecker, "external_power", None)),
        # ---- ab 0.9.10 ----
        # Die Ladesaeule loest der KERN selbst ueber OpenStreetMap auf, sobald
        # geladen wird und die Position bekannt ist; im Connector ist dafuer
        # nichts noetig. Gemessen an carconnectivity 0.11.10,
        # charging.py:77 und charging_station.py:37 ff.
        "ladesaeule_name": str(wert(getattr(saeule, "name", None)) or ""),
        "ladesaeule_betreiber": str(wert(getattr(saeule, "operator_name", None)) or ""),
        "ladesaeule_kw": wert(getattr(saeule, "max_power", None), Power.KW, 1),
    }


def abbild_wartung(fahrzeug, cfg: dict) -> dict:
    from carconnectivity.units import Length
    w = getattr(fahrzeug, "maintenance", None)
    return {
        "inspektion_tage": tage_bis(getattr(w, "inspection_due_at", None)),
        "inspektion_km": wert(getattr(w, "inspection_due_after", None), Length.KM, 0),
        "oelservice_tage": tage_bis(getattr(w, "oil_service_due_at", None)),
        "oelservice_km": wert(getattr(w, "oil_service_due_after", None), Length.KM, 0),
    }


def abbild_position(fahrzeug, cfg: dict) -> dict:
    p = getattr(fahrzeug, "position", None)
    ort = getattr(p, "location", None)
    strasse = " ".join(x for x in (str(wert(getattr(ort, "road", None)) or ""),
                                   str(wert(getattr(ort, "house_number", None)) or "")) if x)
    stadt = str(wert(getattr(ort, "city", None)) or "")
    breite = wert(getattr(p, "latitude", None), None, 6)
    laenge = wert(getattr(p, "longitude", None), None, 6)
    d = {
        "breite": breite,
        "laenge": laenge,
        "hoehe": wert(getattr(p, "altitude", None), None, 0),
        "richtung": wert(getattr(p, "heading", None), None, 0),
        "positionsart": etext(getattr(p, "position_type", None)),
        "strasse": strasse,
        "ort": stadt,
        "adresse": ", ".join(x for x in (strasse, stadt) if x),
    }
    # Heimatort: nur rechnen, wenn BEIDE Seiten bekannt sind. Ein fehlender
    # Heimatort ergibt keinen Wert, nicht "nicht zuhause" - das waere eine
    # Aussage, die niemand geprueft hat.
    hb, hl = cfg.get("heim_breite"), cfg.get("heim_laenge")
    if hb != "" and hl != "" and breite is not None and laenge is not None:
        m = entfernung_m(breite, laenge, hb, hl)
        d["entfernung_m"] = m
        d["zuhause"] = None if m is None else (1 if m <= int(cfg.get("heim_radius") or 150) else 0)
    else:
        d["entfernung_m"] = None
        d["zuhause"] = None
    return d


def fahrzeug_abbilden(fahrzeug, cfg: dict, zyklus: int, stamm: dict) -> dict:
    """Setzt das Abbild eines Fahrzeugs zusammen.

    Jeder Abschnitt einzeln abgesichert: wirft die Bibliothek in einem
    Abschnitt, bleiben die uebrigen gueltig, und der Ausfall wird benannt
    statt verschwiegen.
    """
    ausfaelle: dict[str, str] = {}
    d: dict = {}
    abschnitte = [("stamm", abbild_stamm), ("status", abbild_status),
                  ("reichweite", abbild_reichweite), ("laden", abbild_laden),
                  ("position", abbild_position)]
    if zyklus % cfg["takt_wartung"] == 0 or not stamm:
        abschnitte.append(("wartung", abbild_wartung))
    for name, funktion in abschnitte:
        try:
            d.update(funktion(fahrzeug, cfg))
        except Exception as err:  # noqa: BLE001
            ausfaelle[name] = fehlertext(err)
            melde_gebremst(f"ab_{name}", f"Abschnitt '{name}' konnte nicht gelesen werden: "
                                         f"{ausfaelle[name]}", 900)
    # Den letzten bekannten Wartungsstand weiterreichen - und zwar in BEIDEN
    # Faellen: wenn der Abschnitt ausgelassen wurde UND wenn er ausgefallen ist.
    #
    # Bis 0.9.9 sah die Bedingung nur den ersten Fall. Bei einem Ausfall stand
    # "wartung" ja in abschnitte, der Zweig lief nicht, und der Endpunkt lieferte
    # fuer diesen einen Takt INSPTAGE=-, obwohl der Wert bekannt war. Am
    # Bildschirm flackerte er, in Loxone blieb er stehen. Gemessen am 27.08.2026.
    for k, v in stamm.items():
        if k.startswith(("inspektion", "oelservice")) and d.get(k) is None:
            d[k] = v
    d["ausfaelle"] = ausfaelle
    # Der Ausfalltext geht als eigenes MQTT-Thema hinaus: ein leeres Feld
    # nennt den Grund nicht, und ein Blick ins Protokoll setzt voraus, dass
    # jemand hinsieht.
    d["ausfalltext"] = "; ".join(f"{n}: {t}" for n, t in sorted(ausfaelle.items()))
    d["ok"] = 0 if len(ausfaelle) >= 3 else 1
    return d


# ---------------------------------------------------------------------------
# Verlauf (Ladezustand beziehungsweise Tankfuellstand ueber den Tag)
# ---------------------------------------------------------------------------
def verlauf_anhaengen(nummer: int, stand, reichweite, km, tage: int) -> None:
    """Einen Messpunkt an die Tagesdatei haengen.

    Der mkdir steht in einem try: er wird aus abbild_schreiben() gerufen und
    lag damit bis 0.9.9 ausserhalb jeder Absicherung des Takts. Ein OSError -
    Datentraeger voll, Ordner schreibgeschuetzt - beendete den ganzen Dienst,
    und weil der Sollmerker liegenblieb, startete der Waechter ihn im
    Minutentakt neu.
    """
    if stand is None:
        return
    ordner = PDATA / "verlauf"
    try:
        ordner.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        melde_gebremst("verlauf_ordner", f"Verlaufsordner nicht anlegbar ({err}).")
        return
    datei = ordner / f"fahrzeug{nummer}_{time.strftime('%Y%m%d')}.csv"
    marke = PDATA / f".verlauf_ts_{nummer}"
    letzte = 0
    try:
        letzte = int(marke.read_text())
    except (OSError, ValueError):
        pass
    # Nach UNTEN und nach OBEN gepruefft (seit 0.9.12). Stand hier nur
    # "< 240", hielt ein Rueckwaertssprung der Uhr die Aufzeichnung genau so
    # lange an, wie der Sprung gross war: die Differenz war negativ und damit
    # immer kleiner als 240. Eine Marke, die in der Zukunft liegt, ist keine
    # Wartezeit, sondern ein Grund, neu anzusetzen.
    abstand = time.time() - letzte
    if 0 <= abstand < 240:
        return
    try:
        with datei.open("a", encoding="utf-8") as f:
            f.write(f"{int(time.time())};{stand};"
                    f"{reichweite if reichweite is not None else ''};"
                    f"{km if km is not None else ''}\n")
        marke.write_text(str(int(time.time())))
    except OSError:
        return
    grenze = time.time() - tage * 86400
    for alt in ordner.glob("fahrzeug*_*.csv"):
        try:
            if alt.stat().st_mtime < grenze:
                alt.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Ladevorgaenge und Fahrverbrauch
#
# Beides braucht ZWEI Momentaufnahmen und ist deshalb aus einem Seitenaufruf
# grundsaetzlich nicht erreichbar: der Takt ist die einzige Stelle, die den
# Zustand fortschreibt. Oberflaeche und Endpunkt lesen ihn.
#
# Der fortgeschriebene Zustand liegt in einer eigenen Datei, damit er einen
# Neustart des Dienstes uebersteht. Eine Zweitschrift daneben braucht es nicht:
# Ladeprotokoll und Verbrauch sind neu erzeugbar, ein Merkwort waere es nicht.
# ---------------------------------------------------------------------------
DATEI_FORTSCHREIBUNG = PDATA / "fortschreibung.json"
DATEI_LADUNGEN = PDATA / "ladungen.csv"

# Unter dieser Fahrstrecke ist ein gerechneter Verbrauch Rauschen: der
# Ladezustand wird in ganzen Prozent gemeldet, ein Prozent sind bei 60 kWh
# schon 0,6 kWh. Bei 20 km ergaebe ein einziges Prozent Ablesefehler rund
# 3 kWh/100 km - deshalb wird erst ab dieser Strecke gerechnet.
VERBRAUCH_MIN_KM = 20


def ladung_anhaengen(nummer: int, start: int, ende: int, soc_vor, soc_nach,
                     kwh, km, quelle: str, kosten=None, preis=None) -> None:
    """Einen abgeschlossenen Ladevorgang protokollieren."""
    try:
        neu = not DATEI_LADUNGEN.exists()
        with DATEI_LADUNGEN.open("a", encoding="utf-8") as f:
            if neu:
                f.write("# fahrzeug;start;ende;soc_vor;soc_nach;kwh;km;quelle;kosten_eur;preis_ct\n")
            # Gruppe 41: kosten_eur;preis_ct hinten angehaengt - alte Zeilen bleiben, leer = unbekannt.
            f.write("%d;%d;%d;%s;%s;%s;%s;%s;%s;%s\n" % (
                nummer, start, ende,
                "" if soc_vor is None else soc_vor,
                "" if soc_nach is None else soc_nach,
                "" if kwh is None else round(kwh, 2),
                "" if km is None else km,
                mqtt_wert_saeubern(quelle, 40).replace(";", " "),
                "" if kosten is None or kosten == -1 else kosten,
                "" if preis is None or preis == -1 else preis))
    except OSError as err:
        melde_gebremst("ladungen", f"Ladeprotokoll nicht schreibbar ({err}).")
        return
    # Obergrenze: eine Datei, die nur waechst, ist eine Zeitbombe auf einer
    # Speicherkarte. 2000 Zeilen sind rund 150 kB.
    try:
        zeilen = DATEI_LADUNGEN.read_text(encoding="utf-8").splitlines()
        if len(zeilen) > 2000:
            kopf = [z for z in zeilen[:1] if z.startswith("#")]
            rest = [z for z in zeilen if not z.startswith("#")][-1500:]
            # Erst in eine Nebendatei, dann umbenennen - wie json_schreiben()
            # weiter oben. Bis 0.9.11 stand hier write_text(): das kuerzt die
            # Datei zuerst auf null und schreibt dann rund 110 kB. Ein
            # Stromausfall in diesem Fenster kostete das ganze Ladeprotokoll,
            # ein Seitenaufruf las ein Bruchstueck. Seit dem Durchgang
            # 02.10.2026 ueber nebendatei_schreiben() (.tmp.<pid>, fsync).
            text = "\n".join(kopf + rest) + "\n"
            nebendatei_schreiben(DATEI_LADUNGEN, lambda f: f.write(text))
    except OSError:
        pass


def fortschreiben(nummer: int, f: dict, cfg: dict) -> dict:
    """Ladevorgaenge erkennen und den Fahrverbrauch rechnen.

    Rueckgabe: die Felder, die aus dem Vergleich zweier Momentaufnahmen
    entstehen. Was sich nicht belegen laesst, bleibt leer - auch ein Verbrauch.
    """
    alle = json_lesen(DATEI_FORTSCHREIBUNG)
    v = alle.get(str(nummer)) or {}
    jetzt = int(time.time())
    soc = f.get("soc")
    km = f.get("kilometerstand")
    laedt = f.get("laedt")
    kapazitaet = f.get("batterie_nutzbar_kwh") or f.get("batterie_kwh")
    aus: dict = {}

    # ---- Ladevorgang ----
    war = v.get("laedt")
    if laedt is None:
        # Der Ladezustandsname steht nicht in LADEN_AN. Bis 0.9.11 fiel None
        # durch alle drei Zweige: v["laedt"] wurde auf None gesetzt, und der
        # naechste Wechsel auf 0 fand kein "war == 1" mehr vor - die ganze
        # Ladung fehlte danach im Protokoll, ohne eine Meldung. Beim Verbrauch
        # blieb die Fahrmarke stehen, waehrend zwischendurch geladen wurde;
        # aus 18 kWh/100 km konnte so 1 kWh/100 km werden.
        #
        # Jetzt: der zuletzt BEKANNTE Zustand bleibt stehen, es wird nichts
        # gerechnet, und der unbekannte Name wird einmal am Tag gemeldet -
        # damit er in LADEN_AN nachgetragen werden kann, statt still zu
        # schaden. Fail closed: lieber kein Wert als ein falscher.
        melde_gebremst(
            "ladezustand_unbekannt",
            "Unbekannter Ladezustand '%s' - er steht nicht in LADEN_AN. "
            "Ladeerkennung und Verbrauchsrechnung setzen so lange aus. "
            "Bitte den Namen melden, damit er nachgetragen wird."
            % (f.get("ladezustand_text") or "?"), 86400)
        alle[str(nummer)] = v
        json_schreiben(DATEI_FORTSCHREIBUNG, alle)
        if v.get("verbrauch") is not None:
            aus["verbrauch"] = v["verbrauch"]
        return aus
    if laedt == 1 and war != 1:
        v["lade_start"] = jetzt
        v["lade_soc"] = soc
        v["lade_km"] = km
        # Gruppe 41 (G6): Mitschrift fuer die Ladekosten (nur zu Hause und mit dem Haken; uebersteht Neustarts).
        ladung_beginnen(nummer, f, cfg, jetzt)
    elif laedt == 0 and war == 1 and v.get("lade_start"):
        vor = v.get("lade_soc")
        kwh = None
        if vor is not None and soc is not None and kapazitaet:
            d = float(soc) - float(vor)
            # Ein Ladevorgang, bei dem der Stand faellt, ist keiner. Das kommt
            # vor: das Fahrzeug meldet "charging", waehrend es die Klimaanlage
            # aus der Batterie speist.
            kwh = round(d / 100.0 * float(kapazitaet), 2) if d > 0 else None
        # Gruppe 41 (G6): Kosten und Durchschnittspreis aus den Minutenproben (-1 ohne Aussage, nie geschaetzt).
        kosten, preis = ladung_kosten_abschliessen(nummer, kwh, jetzt)
        ladung_anhaengen(nummer, int(v["lade_start"]), jetzt, vor, soc, kwh,
                         km, f.get("ladesaeule_name") or f.get("ladeart") or "", kosten, preis)
        v.pop("lade_start", None)
        v.pop("lade_soc", None)
        v.pop("lade_km", None)
    v["laedt"] = laedt
    if laedt == 1:
        ladung_leistung(nummer, f)

    # ---- Fahrverbrauch ----
    # Gerechnet wird ueber einen abgeschlossenen Fahrabschnitt: seit der
    # letzten Marke wurde nicht geladen, der Kilometerstand ist um mindestens
    # VERBRAUCH_MIN_KM gestiegen und der Ladezustand ist gefallen.
    if laedt == 1:
        v["fahr_km"] = km
        v["fahr_soc"] = soc
    elif km is not None and soc is not None:
        akm, asoc = v.get("fahr_km"), v.get("fahr_soc")
        if akm is None or asoc is None:
            v["fahr_km"], v["fahr_soc"] = km, soc
        else:
            dkm = float(km) - float(akm)
            dsoc = float(asoc) - float(soc)
            if dkm >= VERBRAUCH_MIN_KM and dsoc > 0 and kapazitaet:
                aus["verbrauch"] = round(dsoc / 100.0 * float(kapazitaet) / dkm * 100.0, 1)
                v["verbrauch"] = aus["verbrauch"]
                v["fahr_km"], v["fahr_soc"] = km, soc
            elif dkm < 0:
                # Kilometerstand kleiner als zuvor: ein Fahrzeugtausch oder ein
                # Fehler der Schnittstelle. Neu ansetzen statt eine negative
                # Strecke zu rechnen.
                v["fahr_km"], v["fahr_soc"] = km, soc
    if "verbrauch" not in aus and v.get("verbrauch") is not None:
        aus["verbrauch"] = v["verbrauch"]

    alle[str(nummer)] = v
    json_schreiben(DATEI_FORTSCHREIBUNG, alle)
    return aus


# ---------------------------------------------------------------------------
# Schreibbefehle aus der Warteschlange
#
# Der Loxone-Endpunkt legt hier eine JSON-Datei ab, der Dienst arbeitet sie ab
# und legt die Antwort daneben. Der Endpunkt selbst spricht NIE mit Volkswagen.
# ---------------------------------------------------------------------------
def antwort_schreiben(kennung: str, ok: int, meldung: str, zusatz: dict | None = None) -> None:
    # mkdir mit Wache: ein OSError (Datentraeger voll, Ordner
    # schreibgeschuetzt) flog bis 0.9.11 aus dieser Funktion heraus und riss
    # die ganze Runde mit - samt der Antworten, auf die der Endpunkt wartet.
    # Dasselbe Muster ist an zwei anderen Stellen dieser Datei bereits als
    # Fehler von 0.9.9 beschrieben und behoben.
    try:
        ORDNER_ANTWORTEN.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        melde_gebremst("antwort_ordner",
                       f"Antwortordner nicht anlegbar ({err}).")
        return
    d = {"ok": ok, "meldung": meldung, "ts": int(time.time())}
    if zusatz:
        d.update(zusatz)
    json_schreiben(ORDNER_ANTWORTEN / f"{kennung}.json", d)
    grenze = time.time() - 900
    for alt in ORDNER_ANTWORTEN.glob("*.json"):
        try:
            if alt.stat().st_mtime < grenze:
                alt.unlink()
        except OSError:
            pass


def nummern_lesen() -> dict:
    """Fahrgestellnummer -> feste Fahrzeugnummer, so wie fahrzeugnummern() sie
    vergeben hat. Nur lesen - vergeben wird ausschliesslich beim Abruf."""
    aus: dict[str, int] = {}
    for v, n in json_lesen(DATEI_NUMMERN).items():
        try:
            aus[str(v).upper()] = int(n)
        except (TypeError, ValueError):
            pass
    return aus


def fahrzeug_vin(f) -> str:
    return str(wert(getattr(f, "vin", None)) or "")


def nummern_im_konto(fahrzeuge: list) -> list:
    """Die festen Nummern der Fahrzeuge, die das Konto gerade fuehrt, aufsteigend."""
    zuordnung = nummern_lesen()
    return sorted(zuordnung[v.upper()] for v in (fahrzeug_vin(f) for f in fahrzeuge)
                  if v and v.upper() in zuordnung)


def fahrzeug_waehlen(fahrzeuge: list, nummer_oder_vin):
    """Nimmt entweder die feste Fahrzeugnummer oder die Fahrgestellnummer.

    DIESELBE NUMMER WIE BEIM LESEN (C1, Durchgang 02.10.2026). Bis 0.9.26 war
    die Nummer hier der Rang in der nach Fahrgestellnummer sortierten Liste,
    beim Lesen dagegen die feste Nummer aus <ordner>.nummern.json. Gemessen
    (Codebericht Nr. 1): kam ein Fahrzeug mit kleinerer VIN dazu, las
    fahrzeug=1 das eine Auto und schaltete das andere - mit OK=1; verliess
    Auto 1 das Konto, schaltete fahrzeug=1 das verbliebene Auto 2. Jetzt geht
    der Weg Nummer -> VIN -> Fahrzeugobjekt; eine Nummer, deren Fahrzeug das
    Konto nicht mehr fuehrt, wird abgewiesen.

    Abgewiesen, nicht zurechtgebogen: eine VIN mit einem falschen Zeichen
    ergibt kein Fahrzeug (seit 0.9.10). Leer oder gar nicht angegeben ist die
    Nummer 1 - das ist die Vorgabe, nicht ein missratener Wert.
    """
    s = str(nummer_oder_vin or "").strip() or "1"
    for f in fahrzeuge:
        v = fahrzeug_vin(f)
        if v and v.upper() == s.upper():
            return f
    if not s.isdigit():
        return None
    n = int(s)
    for vin, nr in nummern_lesen().items():
        if nr == n:
            for f in fahrzeuge:
                if fahrzeug_vin(f).upper() == vin:
                    return f
            return None
    return None


def befehl_holen(objekt, name: str):
    """Holt einen Befehl aus einem Befehlsverzeichnis, oder None."""
    cmds = getattr(objekt, "commands", None)
    verzeichnis = getattr(cmds, "commands", None) or {}
    return verzeichnis.get(name)


# ---------------------------------------------------------------------------
# Drosselung
#
# Volkswagen weist zu haeufige Anfragen mit HTTP 429 ab, und wer im gleichen
# Takt weiter anklopft, verlaengert die Sperre. Beim Ueberschussladen liefert
# Loxone denselben Ladestrom-Sollwert im Sekundentakt - ohne Entprellung
# entstuenden daraus dreitausend Schreibbefehle in der Stunde.
#
# Drei Bremsen, alle einstellbar:
#   abstand_abruf    Mindestabstand zwischen zwei Sofortabrufen
#   befehle_stunde   Hoechstzahl schreibender Befehle je gleitender Stunde
#   entprellung      Sperrzeit fuer denselben Befehl mit demselben Wert
#
# Ein gedrosselter Befehl wird ABGEWIESEN und gemeldet, nicht stillschweigend
# verschluckt: ein Formular, das wortlos nichts tut, schickt den Anwender auf
# die Suche nach einem Fehler, den es nicht gibt.
# ---------------------------------------------------------------------------
class Bremse:
    """Fuehrt Buch ueber abgesetzte Befehle. Lebt im Dienstprozess.

    FRAGEN UND BUCHEN SIND ZWEI SCHRITTE (seit 0.9.12).

    Bis 0.9.11 buchte befehl_erlaubt() sofort beim Fragen - also auch fuer
    Befehle, die unmittelbar danach an einer Wertpruefung scheiterten und
    Volkswagen nie erreichten. Gemessen am 03.09.2026 mit den Vorgabewerten
    (befehle_stunde 30, entprellung 20): sechs inhaltlich falsche Befehle
    ergaben "gebucht=6, gesendet=0"; nach dreissig solchen Versuchen wurde ein
    GUELTIGER Befehl abgewiesen mit "In der letzten Stunde sind bereits 30
    Befehle abgesetzt worden", obwohl kein einziger hinausgegangen war.
    Gegenprobe am Hauswerkzeug vw_befehlstest.py: mit der alten Bremse sechs
    Fehlschlaege und ein Absturz, mit abgeschalteter Bremse 67 bestanden.

    Die Bremse soll das Konto vor Volkswagen schuetzen. Gezaehlt wird deshalb,
    was wirklich hinausgeht: darf() fragt, gebucht() schreibt, und gebucht()
    ruft nur die Huelle befehl_ausfuehren(), wenn der Befehl abgesetzt wurde.

    GERECHNET WIRD MIT time.monotonic(), NICHT MIT time.time() (seit 0.9.12).
    Ein Raspberry ohne Echtzeituhr springt beim ersten Zeitabgleich - die
    Datei nennt den Fall an anderer Stelle selbst. Sprang die Uhr rueckwaerts,
    standen Zeitmarken in der Zukunft: _aufraeumen() raeumte sie nicht ab, das
    Stundenkontingent blieb gesperrt, und abruf_erlaubt() meldete eine
    Restzeit von Stunden. monotonic() kennt keinen Sprung; ein Neustart setzt
    sie zurueck, und das ist genau richtig, weil die Bremse ohnehin nur im
    Dienstprozess lebt.

    'letzter_abruf' ist None, nicht 0.0: unter monotonic() waere 0.0 ein
    Zeitpunkt kurz vor dem Prozessstart, und der erste Sofortabruf nach dem
    Start wuerde mit "Noch 115 s" abgewiesen.
    """

    def __init__(self):
        self.letzte: dict[str, tuple[float, str]] = {}
        self.stunde: list[float] = []
        self.letzter_abruf: float | None = None
        self._vorgemerkt: tuple[str, str] | None = None

    def _aufraeumen(self, jetzt: float) -> None:
        grenze = jetzt - 3600
        self.stunde = [t for t in self.stunde if t >= grenze]

    def abruf_erlaubt(self, cfg: dict) -> tuple[bool, str]:
        abstand = int(cfg.get("abstand_abruf") or 0)
        if abstand <= 0:
            return (True, "")
        jetzt = time.monotonic()
        if self.letzter_abruf is not None:
            rest = int(self.letzter_abruf + abstand - jetzt)
            if rest > 0:
                return (False, f"Der letzte Sofortabruf ist keine {abstand} s her. "
                               f"Noch {rest} s. Die Wartezeit steht im Reiter Einstellungen; "
                               f"zu haeufige Anfragen weist Volkswagen mit HTTP 429 ab.")
        self.letzter_abruf = jetzt
        return (True, "")

    def darf(self, cfg: dict, schluessel: str, wert_text: str) -> tuple[bool, str]:
        """Fragt, ob der Befehl hinausgehen darf. BUCHT NICHTS.

        Gebucht wird erst in gebucht(), und zwar nur, wenn der Befehl
        wirklich abgesetzt wurde.
        """
        self._vorgemerkt = None
        jetzt = time.monotonic()
        self._aufraeumen(jetzt)
        entprellung = int(cfg.get("entprellung") or 0)
        if entprellung > 0 and schluessel in self.letzte:
            zeit, alt = self.letzte[schluessel]
            if alt == wert_text and jetzt - zeit < entprellung:
                return (False, f"Derselbe Befehl mit demselben Wert liegt weniger als "
                               f"{entprellung} s zurueck. Er wird nicht noch einmal gesendet.")
        hoechst = int(cfg.get("befehle_stunde") or 0)
        if hoechst > 0 and len(self.stunde) >= hoechst:
            return (False, f"In der letzten Stunde sind bereits {len(self.stunde)} Befehle "
                           f"abgesetzt worden - das ist die eingestellte Obergrenze. "
                           f"Volkswagen sperrt ein Konto, das zu oft schreibt.")
        self._vorgemerkt = (schluessel, wert_text)
        return (True, "")

    def gebucht(self) -> None:
        """Bucht den zuletzt mit darf() freigegebenen Befehl.

        Wird von der Huelle befehl_ausfuehren() gerufen, nachdem der Befehl
        abgesetzt wurde. Ohne Vormerkung geschieht nichts - der Sofortabruf
        etwa laeuft ueber abruf_erlaubt() und hat nie eine.
        """
        if self._vorgemerkt is None:
            return
        schluessel, wert_text = self._vorgemerkt
        self._vorgemerkt = None
        jetzt = time.monotonic()
        self.stunde.append(jetzt)
        self.letzte[schluessel] = (jetzt, wert_text)

    def verwerfen(self) -> None:
        """Vormerkung fallen lassen - der Befehl ging nicht hinaus."""
        self._vorgemerkt = None


_BREMSE = Bremse()

# Die Ja/Nein-Einstellungen, die 'einstellung&name=...' setzen kann.
# Muessen zu vw_schalter() in webfrontend/html/vw_lib.php passen.
#
# AM GERAET GEMESSEN am 06.09.2026, Connector 0.10.6 (bis 0.9.13 stand hier
# "UNGEMESSEN"): der Connector macht genau zwoelf Fahrzeugattribute schreibbar
# (_is_changeable = True, connector.py:1119-1411). Drei der vier bisherigen
# Schalter sind darunter und tragen zusaetzlich einen Schreibhaken
# (_add_on_set_hook) - sie wirken.
SCHALTER = {
    "klima_entriegeln": ("climatization", "climatization_at_unlock"),
    "klima_ohne_netz":  ("climatization", "climatization_without_external_power"),
    "stecker_auto":     ("charging", "auto_unlock"),
}

# Bekannt, aber NICHT schreibbar - mit dem Grund im Klartext.
#
# 'sitzheizung' stand bis 0.9.13 in SCHALTER und war in 0.9.12 sogar der
# einzige Schalter der Ausgangsvorlage. Am Geraet gemessen ist
# 'seat_heating' im Connector KEIN eigener Schalter, sondern ein abgeleiteter
# Anzeigewert: er wird wahr, sobald eine der vier Sitzzonen an ist
# (connector.py:1191-1199), und hat weder _is_changeable noch einen
# Schreibhaken. Ein Schreibversuch wirft
# "TypeError: You cannot set this attribute. Attribute is not mutable."
#
# Warum die Absage hier steht und der Name nicht einfach verschwindet: wer
# die Adresse im Miniserver eingetragen hat, bekommt sonst nur "Unbekannte
# Einstellung" und sucht den Fehler bei sich. Ein Name, der fallen soll,
# faellt angekuendigt und benannt - er behaelt seine Bedeutung als LESENDER
# Wert (Thema fahrzeugN/sitzheizung_ein, Endpunktfeld SITZH).
NUR_LESEND = {
    "sitzheizung":
        "Die Sitzheizung laesst sich ueber den Volkswagen-Connector nicht "
        "setzen. Am Geraet gemessen (Connector 0.10.6, 06.09.2026): "
        "'seat_heating' ist dort kein eigener Schalter, sondern ein "
        "abgeleiteter Anzeigewert - er ist wahr, sobald eine der vier "
        "Sitzzonen an ist. Der Wert wird weiterhin GELESEN: Thema "
        "'<praefix>/fahrzeugN/sitzheizung_ein' und das Feld SITZH der "
        "Statusantwort. Schaltbar sind: "
        + ", ".join(sorted(SCHALTER)) + ".",
}


def _befehl_absetzen(fahrzeuge: list, cfg: dict, b: dict) -> tuple[int, str, dict]:
    """Rueckgabe: (ok, Meldung, Zusatzfelder). ok = 1 angenommen, 0 abgelehnt.

    Was "angenommen" heisst: die Bibliothek setzt den Befehl als HTTP-Anfrage
    an den Volkswagen-Server ab und wirft, wenn der nicht mit 200 antwortet.
    ok = 1 bedeutet also: der Server hat den Auftrag entgegengenommen. Ob das
    Fahrzeug ihn ausfuehrt, zeigt erst der naechste Abruf - das steht auch so
    in jeder Antwort.
    """
    aktion = str(b.get("aktion") or "")

    if aktion == "abruf":
        ok, meldung = _BREMSE.abruf_erlaubt(cfg)
        if not ok:
            return (0, meldung, {})
        return (1, "Sofortabruf eingeplant.", {})

    if not cfg.get("steuerung_ein"):
        return (0, "Die Steuerung ist ausgeschaltet. Reiter Einstellungen, "
                   "Haken 'Schreibende Befehle zulassen'.", {})

    # Der zweite Haken. Ver- und Entriegeln, Hupe und Lichthupe oeffnen das
    # Fahrzeug beziehungsweise machen es auffindbar - sie haengen an einem
    # eigenen Schalter, der ab Werk aus ist.
    if aktion in EINGREIFEND and not cfg.get("eingreifend_ein"):
        return (0, "Eingreifende Befehle sind gesperrt. Reiter Einstellungen, "
                   "Haken 'Eingreifende Befehle zulassen'.", {})

    if not fahrzeuge:
        return (0, "Es ist noch kein Fahrzeug bekannt. Erst einen Abruf abwarten.", {})

    f = fahrzeug_waehlen(fahrzeuge, b.get("fahrzeug"))
    if f is None:
        return (0, f"Fahrzeug '{b.get('fahrzeug')}' gibt es nicht. "
                   f"Das Konto fuehrt {len(fahrzeuge)} Fahrzeuge mit den festen Nummern "
                   f"{', '.join(str(n) for n in nummern_im_konto(fahrzeuge)) or '-'}. "
                   f"Angegeben werden koennen die feste Nummer oder die vollstaendige "
                   f"Fahrgestellnummer.", {})
    vin = str(wert(getattr(f, "vin", None)) or "")

    # Drosselung. Der Schluessel enthaelt das Fahrzeug, damit zwei Autos
    # einander nicht bremsen.
    _dro_wert = ";".join(str(b.get(k, "")) for k in ("temp", "prozent", "ampere", "name", "wert"))
    ok, meldung = _BREMSE.darf(cfg, f"{vin or '1'}:{aktion}", _dro_wert)
    if not ok:
        return (0, meldung, {"vin": vin})

    nachsatz = (" Der Volkswagen-Server hat den Auftrag angenommen; ob das Fahrzeug ihn "
                "ausfuehrt, zeigt der naechste Abruf.")

    def fehlt(was: str) -> tuple[int, str, dict]:
        return (0, f"Dieses Fahrzeug bietet '{was}' nicht an. Entweder kann es das nicht, "
                   f"oder die Funktion ist im Volkswagen-Konto nicht freigeschaltet.", {})

    if aktion in ("klima_start", "klima_stop"):
        cmd = befehl_holen(getattr(f, "climatization", None), "start-stop")
        if cmd is None:
            return fehlt("Klimatisierung")
        if aktion == "klima_stop":
            cmd.value = "stop"
            return (1, "Klimatisierung aus angefordert." + nachsatz, {"vin": vin})
        temp = wert_zahl(b.get("temp"))
        if temp is None:
            return (0, "Die Zieltemperatur fehlt oder ist keine Zahl.", {})
        lo, hi = cfg["temp_min"], cfg["temp_max"]
        if temp < lo or temp > hi:
            # Abweisen, nicht zurechtbiegen: ein still gekappter Sollwert
            # fuehrt zu einem Fahrzeug, das etwas anderes tut als angezeigt.
            return (0, f"Zieltemperatur {temp} Grad liegt ausserhalb der eingestellten Grenzen "
                       f"({lo} bis {hi} Grad). Grenzen im Reiter Einstellungen anpassen.", {})
        cmd.value = f"start --target-temperature {temp} --target-temperature-unit °C"
        return (1, f"Klimatisierung mit {temp} Grad angefordert." + nachsatz,
                {"temp": temp, "vin": vin})

    if aktion == "zieltemperatur":
        temp = wert_zahl(b.get("temp"))
        if temp is None:
            return (0, "Die Zieltemperatur fehlt oder ist keine Zahl.", {})
        lo, hi = cfg["temp_min"], cfg["temp_max"]
        if temp < lo or temp > hi:
            return (0, f"Zieltemperatur {temp} Grad liegt ausserhalb der eingestellten Grenzen "
                       f"({lo} bis {hi} Grad).", {})
        einst = getattr(getattr(f, "climatization", None), "settings", None)
        attr = getattr(einst, "target_temperature", None)
        if attr is None:
            return fehlt("Zieltemperatur")
        attr.value = float(temp)
        return (1, f"Zieltemperatur {temp} Grad gesetzt." + nachsatz, {"temp": temp, "vin": vin})

    if aktion in ("laden_start", "laden_stop"):
        cmd = befehl_holen(getattr(f, "charging", None), "start-stop")
        if cmd is None:
            return fehlt("Laden steuern")
        cmd.value = "start" if aktion == "laden_start" else "stop"
        return (1, ("Laden starten angefordert." if aktion == "laden_start"
                    else "Laden anhalten angefordert.") + nachsatz, {"vin": vin})

    if aktion == "ladegrenze":
        p = wert_zahl(b.get("prozent"))
        if p is None:
            return (0, "Der Prozentwert fuer die Ladegrenze fehlt oder ist keine Zahl.", {})
        einst = getattr(getattr(f, "charging", None), "settings", None)
        attr = getattr(einst, "target_level", None)
        if attr is None:
            return fehlt("Ladegrenze")
        # Die Grenzen der BIBLIOTHEK lesen, nicht raten. Der Connector setzt
        # fuer target_level minimum 50, maximum 100 und precision 10; bis
        # 0.9.9 liess das Plugin 10 bis 100 zu, und ein Wert darunter kam als
        # rohes "ValueError: Value 20.0% is below minimum 50.0%" beim Anwender
        # an. Am Schwester-Connector gemessen; fuer den Volkswagen-Connector
        # UNGEMESSEN, deshalb mit Rueckfall auf die weiten Grenzen.
        lo = ganz(getattr(attr, "minimum", None), 10)
        hi = ganz(getattr(attr, "maximum", None), 100)
        lo, hi = max(0, min(lo, 100)), max(0, min(hi, 100))
        if lo > hi:
            lo, hi = 10, 100
        if p < lo or p > hi:
            return (0, f"{p} % ist keine zulaessige Ladegrenze. Dieses Fahrzeug nimmt "
                       f"{lo} bis {hi} % an.", {})
        attr.value = float(p)
        return (1, f"Ladegrenze {p} % gesetzt (Volkswagen rundet auf die Stufen, die das "
                   f"Fahrzeug kennt - meist Zehnerschritte)." + nachsatz,
                {"prozent": p, "vin": vin})

    if aktion == "ladestrom":
        a = wert_zahl(b.get("ampere"))
        if a is None:
            return (0, "Der Ampere-Wert fehlt oder ist keine Zahl.", {})
        # Gerundet, nicht abgeschnitten: int(16.9) waere 16 gewesen, int(15.9)
        # dagegen 15 und damit abgewiesen. Zwei benachbarte Eingaben, zwei
        # verschiedene Sorten Antwort - das ist keine Grenze, das ist Zufall.
        a_ganz = int(round(float(a)))
        if a_ganz not in LADESTROM_STUFEN:
            return (0, f"{a} A ist keine zulaessige Stufe. Zulaessig sind: "
                       f"{', '.join(str(x) for x in LADESTROM_STUFEN)} A. "
                       f"Welche davon Ihr Fahrzeug kennt, haengt vom Modell ab: manche fuehren "
                       f"den Strom in Ampere (5/10/13/32), die uebrigen kennen nur reduziert (6) "
                       f"und maximal (16).", {})
        einst = getattr(getattr(f, "charging", None), "settings", None)
        attr = getattr(einst, "maximum_current", None)
        if attr is None:
            return fehlt("Ladestrom")
        attr.value = float(a_ganz)
        return (1, f"Ladestrom {a_ganz} A gesetzt." + nachsatz, {"ampere": a_ganz, "vin": vin})

    if aktion in ("scheibe_ein", "scheibe_aus"):
        cmd = befehl_holen(getattr(f, "window_heatings", None), "start-stop")
        if cmd is None:
            return fehlt("Scheibenheizung")
        cmd.value = "start" if aktion == "scheibe_ein" else "stop"
        return (1, ("Scheibenheizung ein angefordert." if aktion == "scheibe_ein"
                    else "Scheibenheizung aus angefordert.") + nachsatz, {"vin": vin})

    if aktion == "wecken":
        cmd = befehl_holen(f, "wake-sleep")
        if cmd is None:
            return fehlt("Wecken")
        cmd.value = "wake"
        return (1, "Weckruf gesendet." + nachsatz, {"vin": vin})

    # ---- ab 0.9.10: eingreifende Befehle ----
    #
    # Beide Befehlsklassen liegen im Kern von carconnectivity (gemessen an
    # 0.11.10: LockUnlockCommand, HonkAndFlashCommand). Ob der
    # Volkswagen-Connector sie registriert, ist UNGEMESSEN - hier liegt kein
    # Fahrzeug. Fehlt der Befehl, sagt fehlt() es klar, statt zu raten.
    if aktion in ("verriegeln", "entriegeln"):
        if not zugang().get("spin"):
            return (0, "Ver- und Entriegeln verlangt die vierstellige S-PIN des "
                       "Volkswagen-Kontos. Reiter Einstellungen.", {})
        cmd = befehl_holen(f, "lock-unlock")
        if cmd is None:
            return fehlt("Ver- und Entriegeln")
        cmd.value = "lock" if aktion == "verriegeln" else "unlock"
        return (1, ("Verriegeln angefordert." if aktion == "verriegeln"
                    else "Entriegeln angefordert.") + nachsatz, {"vin": vin})

    if aktion in ("blinken", "hupen"):
        cmd = befehl_holen(f, "honk-flash")
        if cmd is None:
            return fehlt("Hupe und Lichthupe")
        # Der Kern kennt 'flash' und 'honk-and-flash'; ein Hupen ohne Licht
        # gibt es nicht. Die Beschriftung sagt das, statt es zu verschweigen.
        cmd.value = "flash" if aktion == "blinken" else "honk-and-flash"
        return (1, ("Lichthupe angefordert." if aktion == "blinken"
                    else "Hupe und Lichthupe angefordert.") + nachsatz, {"vin": vin})

    if aktion == "einstellung":
        name = str(b.get("name") or "")
        if name in NUR_LESEND:
            return (0, NUR_LESEND[name], {"name": name})
        if name not in SCHALTER:
            return (0, f"Unbekannte Einstellung '{name}'. Bekannt sind: "
                       f"{', '.join(sorted(SCHALTER))}.", {})
        w = schalt(b.get("wert"), -1)
        if w < 0:
            return (0, "Der Wert muss 0 oder 1 sein.", {})
        bereich, feld = SCHALTER[name]
        einst = getattr(getattr(f, bereich, None), "settings", None)
        attr = getattr(einst, feld, None)
        if attr is None:
            return fehlt(name)
        attr.value = bool(w)
        return (1, f"Einstellung '{name}' auf {w} gesetzt." + nachsatz,
                {"name": name, "wert": w, "vin": vin})

    return (0, f"Unbekannte Aktion '{aktion}'.", {})


def wert_zahl(v):
    """Zahl aus einem Befehlsparameter. Keine Zahl ergibt None, nicht 0."""
    if v is None or v == "":
        return None
    try:
        f = float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else round(f, 1)


def befehl_ausfuehren(fahrzeuge: list, cfg: dict, b: dict) -> tuple[int, str, dict]:
    """Fuehrt einen Befehl aus, mit Wecker, und bucht ihn ERST DANACH.

    HIER liegt seit 0.9.12 die Zeitgrenze - um den EINZELNEN Befehl.

    Bis 0.9.11 lag sie in der Warteschleife um den Aufruf von
    warteschlange(), also um einen ganzen Durchgang durch ALLE vorliegenden
    Befehlsdateien. Das hatte zwei Wirkungen, beide am Quelltext gemessen:
    liegen drei Befehle zu je 50 s an, schlug der Wecker beim dritten zu,
    obwohl keiner hing - und weil TimeoutError eine Exception ist, fing die
    Schleife in warteschlange() sie je Befehl ab, ohne den Wecker neu zu
    stellen: alle folgenden Befehle derselben Runde liefen danach voellig
    ungeschuetzt, mit den vollen rund 540 s der Bibliothek.
    Ausserdem stand abfahrt_pruefen() auf der Einrueckungsebene des
    with-Blocks, lief also NACH dessen __exit__ und damit ohne jeden Wecker -
    obwohl gerade die Vorklimatisierung ein Schreibbefehl ist.

    An dieser Stelle ist jeder Befehl geschuetzt, gleich von wo er kommt:
    aus der Warteschlange, aus abfahrt_pruefen() oder aus einem spaeteren
    Aufrufer.

    Die Reihenfolge ist der zweite Zweck dieser Huelle: _befehl_absetzen()
    fragt die Bremse mit darf() (die nichts bucht), prueft danach Fahrzeug,
    Faehigkeit, Wertebereich und S-PIN, und erst wenn der Befehl wirklich an
    Volkswagen gegangen ist, wird er gezaehlt.

    ok == 1 heisst "abgesetzt". ok == 0 heisst abgewiesen - dann wird die
    Vormerkung verworfen, damit sie nicht bei einem spaeteren Befehl
    faelschlich gebucht wird.
    """
    try:
        with Zeitgrenze(GRENZE_BEFEHL, "Ein Schreibbefehl an Volkswagen"):
            ok, meldung, zusatz = _befehl_absetzen(fahrzeuge, cfg, b)
    except TimeoutError as err:
        # Der Wecker hat zugeschlagen. Gebucht wird nichts: ob der Befehl
        # bei Volkswagen angekommen ist, weiss hier niemand, und eine
        # Buchung wuerde das Kontingent fuer etwas verbrauchen, das
        # vielleicht nie hinausging.
        _BREMSE.verwerfen()
        return (0, fehlertext(err), {})
    if ok == 1:
        _BREMSE.gebucht()
    else:
        _BREMSE.verwerfen()
    return (ok, meldung, zusatz)

def warteschlange(fahrzeuge: list, cfg: dict) -> bool:
    """Arbeitet alle vorliegenden Befehle ab. True, wenn ein Sofortabruf
    angefordert wurde."""
    try:
        ORDNER_BEFEHLE.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        melde_gebremst("befehlsordner",
                       f"Warteschlangenordner nicht anlegbar ({err}).")
        return False
    sofort = False
    for datei in sorted(ORDNER_BEFEHLE.glob("*.json")):
        b = json_lesen(datei)
        kennung = datei.stem
        # NUR WER DIE DATEI WEGNIMMT, FUEHRT AUS (C3, Durchgang 02.10.2026).
        # Bis 0.9.26 stand hier "except OSError: pass": liefen zwei Dienste,
        # lasen beide dieselbe Datei, der zweite scheiterte am Loeschen -
        # und fuehrte trotzdem aus. Gemessen (Codebericht Nr. 3): 20 Befehle,
        # 21 Ausfuehrungen am Fahrzeug. Jetzt gilt: misslingt das Entnehmen,
        # hat ein anderer den Befehl - er wird hier nicht ausgefuehrt.
        try:
            datei.unlink()
        except OSError as err:
            melde_gebremst("befehl_entnehmen",
                           f"Befehl {kennung}: die Datei liess sich nicht aus der "
                           f"Warteschlange nehmen ({err}) - er wird hier nicht "
                           f"ausgefuehrt.", 60)
            continue
        if not b:
            antwort_schreiben(kennung, 0, "Befehlsdatei war leer oder unlesbar.")
            continue
        try:
            ok, meldung, zusatz = befehl_ausfuehren(fahrzeuge, cfg, b)
        except Exception as err:  # noqa: BLE001 - jeder Fehler gehoert gemeldet, nicht verschluckt
            ok, meldung, zusatz = 0, fehlertext(err), {}
        antwort_schreiben(kennung, ok, meldung, zusatz)
        _LOG.info("Befehl %s (%s): ok=%s %s", kennung, b.get("aktion"), ok, meldung)
        if b.get("aktion") == "abruf" and ok:
            sofort = True
    return sofort


# ---------------------------------------------------------------------------
# Abbild schreiben
# ---------------------------------------------------------------------------
# Die veroeffentlichten Themen. Muessen zu vw_mqtt_themen() in
# webfrontend/html/vw_lib.php passen; eine Zeile im Reiter Test zaehlt es bei
# jedem Seitenaufbau nach. Die Tabelle dort ist die Anleitung - eine Liste,
# die niemand nachmisst, laeuft auseinander.
#
# NEUE NAMEN WERDEN HINTEN ANGEHAENGT. Umbenennen bricht jede bestehende Anlage.
MQTT_FELDER = (
    "soc", "tank_prozent", "reichweite_km", "kilometerstand", "verriegelt",
    "tueren_offen", "fenster_offen", "licht_an", "handbremse", "zustand",
    "erreichbar", "klima_an", "zieltemperatur", "aussentemperatur",
    "scheibenheizung", "laedt", "ladeleistung_kw", "ladetempo_kmh",
    "ladegrenze", "ladestrom_a", "kabel_verbunden", "stecker_verriegelt",
    "laden_fertig_um", "breite", "laenge", "inspektion_tage", "inspektion_km",
    "oelservice_tage", "oelservice_km",
    # ---- ab 0.9.10 ----
    "reichweite_elektro_km", "reichweite_verbrenner_km", "reichweite_wltp_km",
    "batterie_kwh", "batterie_temp", "oelstand_prozent", "anzahl_antriebe",
    "klima_fertig_um", "sitzheizung_ein", "klima_bei_entriegeln",
    "stecker_entriegeln", "verbrauch", "adblue_km", "tueren_zahl",
    "fenster_zahl", "standzeit_min", "hoehe", "entfernung_m", "zuhause",
    "ladesaeule_kw", "ladeempfehlung", "ladung_kwh", "ladung_dauer_min",
    "ladung_vor_stunden", "tag_kwh", "ladungen_gesamt",
    # ---- ab 0.9.33 (Gruppe 41): Vorklimatisierung (0 nichts, 1 gesendet, 2 mild,
    # 3 Wiederholung, 4 wartet auf Anwesenheit, -1 gescheitert) und Kosten/Preis der
    # letzten Ladung zu Hause (-1 ohne Aussage). Nicht im Abbild - g41_wert() liefert sie.
    "vorklima", "ladung_kosten", "ladung_preis",
    # ---- ab dem Durchgang 02.10.2026 (M8): Lage JE FAHRZEUG, fluechtig ----
    # ok: dieses Fahrzeug hat beim letzten Abruf geantwortet (wie OK am
    # Endpunkt); ts: Zeitpunkt seines letzten gelungenen Abrufs.
    "ok", "ts",
    # ---- Nachtrag 4 (29.10.2026): Restminuten bis Ladeende / Klima-Ende, fluechtig; -1 ohne
    # Aussage, 0 nach dem Ende. Nicht im Abbild - abbild_schreiben() rechnet sie aus der Endzeit.
    "restzeit_min", "klima_restzeit_min",
)

# Themen mit einer Zeichenkette als Nutzlast. Sie bekommen in der Loxone-
# Vorlage KEINEN virtuellen Eingang - ein virtueller Eingang ist eine Zahl.
# Ueber MQTT sind sie trotzdem nuetzlich: der Zustandstext sagt 'conservation',
# wo die Zahl nur 0 sagt.
MQTT_TEXTFELDER = (
    "zustand_text", "klima_text", "ladezustand_text", "ladeart",
    "externe_stromversorgung", "positionsart", "adresse", "modell", "vin",
    "kennzeichen", "software", "tueren_namen", "fenster_namen",
    "ladesaeule_name", "ladesaeule_betreiber", "ausfalltext",
)

# Themen oberhalb der Fahrzeugebene. 'ts' und 'zaehler' sind das Lebenszeichen:
# ueber MQTT gibt es kein Alter, nur einen Zeitstempel, und der Miniserver
# rechnet selbst. Der Zaehler beantwortet, was der Zeitstempel nicht kann - ein
# Raspberry ohne Echtzeituhr springt beim ersten Zeitabgleich.
MQTT_OBEN = (
    "ok", "fahrzeuge", "ts", "zaehler", "fehler_folge", "fehlertext",
)


# Themen, die NICHT behalten werden - Hausstandard seit 03.09.2026:
# Zustaende retained, Messwerte mit Zeitbezug nicht, das Lebenszeichen nie.
#
# Ein behaltener Messwert sieht nach einem Ausfall frisch aus, obwohl er alt
# ist; ein behaltenes Lebenszeichen meldet "lebt", auch wenn der Dienst tot
# ist. Beides ist eine Falschaussage, und beide standen bis 0.9.11 unter
# demselben einen Schalter wie alles uebrige.
#
# Zustaende bleiben behalten: Ladestand, Kilometerstand, Verriegelung,
# Position. Nach einem Neustart des Brokers hat Loxone damit sofort den
# zuletzt gueltigen Stand, statt bis zum naechsten Abruf leer zu bleiben.
#
# Seit 0.9.24 stehen hier auch die AUSSAGEN DES DIENSTES UEBER SICH SELBST -
# Entscheidung des Hausherrn vom 19.09.2026 (Regeln/07, Abschnitt 3):
# 'ok', 'fehler_folge', 'fehlertext' und 'fahrzeugN/ausfalltext' sagen, dass
# der LAUFENDE Dienst gerade keinen Fehler sieht. Stirbt er, bliebe eine
# zurueckbehaltene Null stehen, und nach einem Neustart von Broker oder
# Gateway laese Loxone "alles in Ordnung" von einem Dienst, der nicht mehr
# laeuft. 'fahrzeugN/ausfalltext' war der schwerste der Funde
# (Bestand-2026-09-18/klasse-E, Zeile 140): ein leerer Ausfalltext wird gar
# nicht gesendet, also blieb die letzte Fehlermeldung fuer immer
# zurueckbehalten stehen - auch waehrend der Dienst laeuft und misst.
# Die Prueffrage je Thema lautet: sagt das GERAET das, oder der Dienst ueber
# sich selbst? Alles, was das Geraet sagt, bleibt behalten.
MQTT_OHNE_RETAIN = frozenset((
    # Lebenszeichen - nie behalten
    "ts", "zaehler",
    # Aussagen des Dienstes ueber sich selbst - nie behalten (19.09.2026)
    "ok", "fehler_folge", "fehlertext", "ausfalltext",
    # Leistung und Tempo
    "ladeleistung_kw", "ladetempo_kmh", "ladesaeule_kw",
    # Zeitpunkte und Restzeiten
    "laden_fertig_um", "klima_fertig_um", "standzeit_min", "ladung_vor_stunden",
    # Temperaturen
    "aussentemperatur", "batterie_temp",
    # Tageswerte und Empfehlungen - nie behalten (Entscheidung 8, Regeln/07;
    # M3/M4, Durchgang 02.10.2026). Bis 0.9.26 standen "heute 30,5 kWh" und
    # eine Ladeempfehlung 1 nach Tagen noch retained im Broker (MQTT-Bericht
    # V3, V4).
    "tag_kwh", "ladeempfehlung",
    # Gruppe 41 (0.9.33): Zustand der Vorklimatisierung - eine Aussage des Dienstes.
    "vorklima",
    # Nachtrag 4 (29.10.2026): Restminuten - eine Restzeit, nie behalten.
    "restzeit_min", "klima_restzeit_min",
))

# Genau die Themen, deren zurueckbehaltener Altwert aus den Fassungen bis
# 0.9.23 EINMAL abgeraeumt werden muss. Die Liste ist die Schnittmenge aus
# "neu in MQTT_OHNE_RETAIN" - mehr wird nicht angefasst.
# Seit dem Durchgang 02.10.2026 auch tag_kwh und ladeempfehlung (M3/M4).
MQTT_ALTLAST = ("ok", "fehler_folge", "fehlertext", "ausfalltext",
                "tag_kwh", "ladeempfehlung")

# Kennung des Abraeumens. Sie steht IM Merker, nicht nur in seinem Namen.
# Grund (Regeln/07, Nachtrag 19.09.2026): ein Merker einer Vorfassung darf
# das Abraeumen nicht als "schon erledigt" vortaeuschen. Aendert sich die
# Liste oder das Themenpraefix, laeuft es deshalb erneut.
# "-2" seit dem Durchgang 02.10.2026: die Liste ist laenger geworden, also
# laeuft das Abraeumen einmal erneut.
RETAIN_ABRAEUM_KENNUNG = "dienstzustand-2"

# Zustaende, die nach einem GELUNGENEN Abruf ohne Aussage sind, gehen als "-"
# retained hinaus (Entscheidungen 5 und 8; M1/M2, Durchgang 02.10.2026) - je
# Feld der Abschnitt, der es liefert. Ist der Abschnitt ausgefallen, bleibt
# der Altwert stehen wie bisher. Bis 0.9.26 ging fuer ein leeres Textfeld
# oder eine fehlende Position gar nichts hinaus: "Fahrertuer" und
# "zuhause 1" standen weiter im Broker, waehrend der Endpunkt einen Strich
# lieferte (gemessen, MQTT-Bericht V1, V2).
MQTT_STRICH_ABSCHNITT = {
    "modell": "stamm", "vin": "stamm", "kennzeichen": "stamm", "software": "stamm",
    "zustand_text": "status", "klima_text": "status",
    "tueren_namen": "status", "fenster_namen": "status",
    "ladezustand_text": "laden", "ladeart": "laden", "externe_stromversorgung": "laden",
    "ladesaeule_name": "laden", "ladesaeule_betreiber": "laden",
    "positionsart": "position", "adresse": "position", "breite": "position",
    "laenge": "position", "hoehe": "position", "entfernung_m": "position",
    "zuhause": "position",
}


def fahrzeugnummern(vins: list) -> dict:
    """Ordnet jeder Fahrgestellnummer ihre DAUERHAFTE Fahrzeugnummer zu.

    Warum das sein muss: bis 0.9.11 war die Fahrzeugnummer der Rang in einer
    nach Fahrgestellnummer sortierten Liste. Die Stammdaten wurden schon
    damals ueber die VIN gefuehrt (zwei Zeilen daneben), das Ladeprotokoll,
    die Fortschreibung und die Verlaufsdateien aber ueber diesen Rang. Kommt
    ein Fahrzeug hinzu oder faellt eines weg, verschiebt sich der Rang - und
    das neue Fahrzeug 1 erbt Kilometerstand, Ladezustandsmarke und einen
    offenen Ladevorgang des alten. Der Fall "Kilometerstand kleiner als
    zuvor" war abgefangen, der umgekehrte nicht: aus 78 000 geerbten
    Kilometern und fuenf Prozentpunkten wurde ein Verbrauch, den niemand
    gefahren ist. Ausserdem zeigen alle in Loxone eingetragenen Adressen
    (VW_1_SOC, fahrzeug1/...) danach auf ein anderes Auto.

    Eine Nummer ist eine Adresse (Hausregel): einmal vergeben, wandert sie
    nicht mehr, und nach dem Entfernen eines Fahrzeugs wird sie nicht neu
    vergeben. Es entstehen also Luecken, und das ist richtig.

    AKTUALISIERUNGSFALL: gibt es die Datei noch nicht, gilt die bisherige
    Zaehlung - der Rang in der sortierten Liste. Damit zeigen bestehende
    Adressen nach dem Update weiterhin auf dasselbe Fahrzeug.
    """
    alt = json_lesen(DATEI_NUMMERN)
    zuordnung: dict[str, int] = {}
    for v, n in alt.items():
        try:
            zuordnung[str(v)] = int(n)
        except (TypeError, ValueError):
            pass
    erstlauf = not zuordnung
    vergeben = set(zuordnung.values())
    geaendert = False
    for i, vin in enumerate(vins, start=1):
        if not vin or vin in zuordnung:
            continue
        n = i if erstlauf else 0
        if n <= 0 or n in vergeben:
            n = 1
            while n in vergeben:
                n += 1
        zuordnung[vin] = n
        vergeben.add(n)
        geaendert = True
    if geaendert:
        json_schreiben(DATEI_NUMMERN, zuordnung)
    return zuordnung


def ladebilanz(nummer: int) -> dict:
    """Die Kennzahlen des Ladeprotokolls fuer ein Fahrzeug.

    Gelesen, nicht gerechnet: geschrieben hat sie fortschreiben(). Der Tageswert
    zaehlt, was HEUTE beendet wurde - ein Ladevorgang ueber Mitternacht zaehlt
    zum Tag seines Endes, sonst zaehlte er zweimal oder gar nicht.
    """
    aus = {"ladung_kwh": None, "ladung_dauer_min": None, "ladung_vor_stunden": None,
           "tag_kwh": None, "ladungen_gesamt": None}
    if not DATEI_LADUNGEN.exists():
        return aus
    try:
        zeilen = DATEI_LADUNGEN.read_text(encoding="utf-8").splitlines()
    except OSError:
        return aus
    meine = []
    for z in zeilen:
        if not z or z.startswith("#"):
            continue
        t = z.split(";")
        if len(t) < 6 or ganz(t[0], -1) != nummer:
            continue
        meine.append(t)
    if not meine:
        return aus
    aus["ladungen_gesamt"] = len(meine)
    letzt = meine[-1]
    start, ende = ganz(letzt[1], 0), ganz(letzt[2], 0)
    if letzt[5] != "":
        try:
            aus["ladung_kwh"] = round(float(letzt[5]), 2)
        except ValueError:
            pass
    if ende > start:
        aus["ladung_dauer_min"] = int(round((ende - start) / 60))
        aus["ladung_vor_stunden"] = int(round((time.time() - ende) / 3600))
    tag0 = int(time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d")))
    # Auch nach OBEN begrenzt (seit 0.9.12). Bis 0.9.11 stand hier nur
    # ">= tag0". Eine Zeile, deren Endzeitpunkt durch einen Uhrensprung in
    # der Zukunft liegt - ein Raspberry ohne Echtzeituhr springt beim ersten
    # Zeitabgleich -, uebersprang damit JEDE kuenftige Tagesschwelle und
    # zaehlte an jedem Tag mit, bis sie nach 2000 Zeilen aus der Datei fiel.
    # Die Dauerspalte war gegen denselben Fall abgesichert, die Tagesbilanz
    # nicht. Die Luft von 300 s faengt eine normal nachlaufende Uhr ab.
    obergrenze = int(time.time()) + 300
    summe = 0.0
    hat = False
    for t in meine:
        if tag0 <= ganz(t[2], 0) <= obergrenze and t[5] != "":
            try:
                summe += float(t[5])
                hat = True
            except ValueError:
                pass
    if hat:
        aus["tag_kwh"] = round(summe, 2)
    return aus


# ---------------------------------------------------------------------------
# Gruppe 41, Nachtrag 4 (29.10.2026) - Restminuten und die -1-Regel
#
# RESTMINUTEN: neben den Endzeiten (laden_fertig_um, klima_fertig_um - Unix-Zeit) gehen die Minuten bis dahin
# als eigene Themen hinaus. Bis hier rechnete das Musterprojekt sie per Formel aus der Endzeit, und die haengt an
# der Zeitzone des Miniservers. Ganze Minuten (aufgerundet), >= 0; ohne Aussage -1; liegt das Ende in der
# Vergangenheit 0. Beim Abruf gerechnet und je Minute aus dem Wartetakt nachgefuehrt (restminuten_nachfuehren()).
#
# -1-REGEL (wie BYD 0.9.29; Regeln/07 "MinVal=-1 fuer Felder, die -1 als nicht bekannt senden"): ein MESSWERT
# (fluechtig nach MQTT_OHNE_RETAIN, Zahl), fuer den ein gelungener Abruf keine Aussage hat, geht als -1 hinaus.
# Bis hier ging er gar nicht, und in Loxone stand der letzte Wert und sah gemessen aus. Temperaturen -999 (-1 Grad
# ist eine echte Temperatur). Nur fuer ein Fahrzeug mit ok 1, und nur wenn sein Abschnitt geantwortet hat - sonst
# bleibt der Altwert stehen wie bisher. Zustaende (retained) ohne Aussage bleiben "-" bzw. ungesendet wie bisher
# (MQTT_STRICH_ABSCHNITT), Texte unveraendert. Nur MQTT: das Abbild und damit die HTTP-Antworten bleiben gleich.
# ---------------------------------------------------------------------------
RESTMIN_THEMEN = {"restzeit_min": "laden_fertig_um", "klima_restzeit_min": "klima_fertig_um"}
# Welle Mehrfach-Ziele Los B, Zusatz "Ladeempfehlung ohne -1" (29.10.2026): die Ladeempfehlung ist ein Schaltsignal
# (1 = jetzt laden, 0 = nicht) - ohne Aussage geht 0 hinaus, nie -1 (in Loxone hiesse -1 "ungleich 0" = laden).
# Regel wie Tibber 0.9.33 "Schaltsignale ohne -1". Das Abbild (HTTP) bleibt unveraendert.
MQTT_FEHLWERT = {"aussentemperatur": -999, "batterie_temp": -999, "ladeempfehlung": 0}
MQTT_MESSWERT_ABSCHNITT = {
    "aussentemperatur": "status", "klima_fertig_um": "status", "standzeit_min": "status",
    "klima_restzeit_min": "status", "batterie_temp": "reichweite",
    "ladeleistung_kw": "laden", "ladetempo_kmh": "laden", "laden_fertig_um": "laden", "ladesaeule_kw": "laden",
    "restzeit_min": "laden",
}
# Aussagen des Dienstes, die nie ohne Wert sind - die Regel laesst sie in Ruhe. vorklima traegt -1 schon als
# "gescheitert"; ein -1 ohne Aussage waere dort doppeldeutig.
MQTT_OHNE_FEHLWERT = ("ok", "ts", "vorklima")
_RESTMIN = {"quelle": {}, "gesendet": {}}


def restminuten(ende, jetzt: float) -> int:
    """Ganze Minuten bis 'ende' (Unix-Zeit), aufgerundet; 0 nach dem Ende, -1 ohne Aussage (rein)."""
    if isinstance(ende, bool):
        return -1
    try:
        e = int(ende)
    except (TypeError, ValueError, OverflowError):
        return -1
    if e <= 0:
        return -1
    if e <= jetzt:
        return 0
    return int(math.ceil((e - jetzt) / 60.0))


def mqtt_messwert(feld: str, wert, ausfaelle=None):
    """Der Wert eines Fahrzeugthemas fuer MQTT nach der -1-Regel (rein). None heisst: nicht senden."""
    if wert is not None and not (isinstance(wert, str) and wert.strip() == ""):
        return wert
    if feld not in MQTT_FELDER or not mqtt_ohne_retain(feld) or feld in MQTT_OHNE_FEHLWERT:
        return wert
    abschnitt = MQTT_MESSWERT_ABSCHNITT.get(feld, "")
    if abschnitt and isinstance(ausfaelle, dict) and abschnitt in ausfaelle:
        return None             # Abschnitt ausgefallen: der Altwert bleibt stehen
    return MQTT_FEHLWERT.get(feld, -1)


def restminuten_nachfuehren(cfg: dict, jetzt=None):
    """Je Minute aus dem Wartetakt: die Restminuten aus der zuletzt gemeldeten Endzeit neu rechnen und nur
    Geaendertes senden. Quelle ist der letzte gelungene Abruf (abbild_schreiben()); bei einer Stoerung leer."""
    if not cfg.get("mqtt_ein") or not _RESTMIN["quelle"]:
        return (0, 0)
    jetzt = time.time() if jetzt is None else jetzt
    p = {}
    for nummer, q in _RESTMIN["quelle"].items():
        for thema, ende in q.items():
            k = "fahrzeug%s/%s" % (nummer, thema)
            w = restminuten(ende, jetzt)
            if _RESTMIN["gesendet"].get(k) != w:
                p[k] = w
    if not p:
        return (0, 0)
    _RESTMIN["gesendet"].update(p)
    praefix = str(cfg.get("mqtt_topic") or "volkswagen").strip("/") or "volkswagen"
    return mqtt_senden(p, praefix, 1 if cfg.get("mqtt_retain") else 0)


def abbild_schreiben(stand: dict, cfg: dict, ok: int, fehler: str = "",
                     grund: str = "", zaehler: int = -1, fehler_folge: int = 0,
                     entfernt=()) -> dict:
    """Schreibt den Zwischenspeicher.

    Bei einem fehlgeschlagenen Abruf bleiben die zuletzt gueltigen Werte
    stehen, und der Zeitstempel wird NICHT aufgefrischt. Beides mit Absicht:
    sonst meldete der Endpunkt ploetzlich FAHRZEUG_UNBEKANNT, obwohl nur eine
    Anfrage schiefging, und ALTER bliebe klein - woran aber die
    Ausfallerkennung in Loxone haengt.

    Das LEBENSZEICHEN geht dagegen bei JEDEM Durchgang hinaus, auch bei einer
    Stoerung: ein virtueller Eingang behaelt seinen letzten Wert, bei MQTT mit
    Retain sogar ueber jeden Neustart des Miniservers hinweg. Ein toter Dienst
    saehe sonst aus wie ein ruhiges Haus - das ist keine fehlende Auskunft,
    sondern eine Falschaussage.
    """
    fahrzeuge = stand.get("fahrzeuge") or {}
    lox = {
        "ok": ok,
        "fehler": fehler,
        "grund": grund,
        "zaehler": zaehler,
        "fehler_folge": fehler_folge,
        "letzter_versuch": int(time.time()),
        "anzahl_fahrzeuge": len(fahrzeuge),
        "fahrzeuge": fahrzeuge,
    }
    if stand.get("ts"):
        lox["ts"] = int(stand["ts"])
    # 0600: das Abbild traegt Position und Fahrgestellnummer (C4, "Rechte nach
    # Inhalt"; bis 0.9.26 mit den Rechten der umask). Der Endpunkt und die
    # Oberflaeche laufen als derselbe Benutzer.
    json_schreiben(DATEI_LOXONE, lox, 0o600)
    json_schreiben(DATEI_CACHE, {"letzter_versuch": int(time.time()), "ok": ok,
                                 "fehler": fehler, "fahrzeuge": fahrzeuge}, 0o600)

    praefix = str(cfg.get("mqtt_topic") or "volkswagen").strip("/") or "volkswagen"
    retain = 1 if cfg.get("mqtt_retain") else 0

    # Alte Praefixe abraeumen - auch bei "MQTT aus" (M6, Durchgang 02.10.2026).
    # Ein Fehlschlag darf den Durchgang nicht anhalten.
    try:
        mqtt_praefixe_pflegen(cfg)
    except Exception as err:  # noqa: BLE001
        _LOG.warning("MQTT: Abraeumen alter Praefixe uebersprungen (%s).", fehlertext(err))

    # Einmalig die zurueckbehaltenen Dienstaussagen frueherer Fassungen
    # abraeumen - VOR dem Senden, damit der gueltige Wert unmittelbar
    # hinterhergeht (Regeln/07: eine leere Nutzlast loescht den virtuellen
    # Eingang in Loxone, bis der naechste Wert kommt). Solange es nicht
    # nachweislich durch ist, laeuft es beim naechsten Durchgang wieder; der
    # Merker faellt erst nach dem Nachlesen am Broker. Ein Fehlschlag darf
    # den Durchgang nicht anhalten.
    if cfg.get("mqtt_ein"):
        try:
            mqtt_altlast_abraeumen(praefix)
        except Exception as err:  # noqa: BLE001
            _LOG.warning("MQTT: Abraeumen der Altwerte uebersprungen (%s).",
                         fehlertext(err))

    # Das Lebenszeichen - immer, und bei einer Stoerung ohne die Messwerte.
    # Die alten Messwerte erneut zu veroeffentlichen liesse sie frisch aussehen.
    oben = {
        "ok": ok,
        "fahrzeuge": len(fahrzeuge),
        "ts": int(stand.get("ts") or 0),
        "zaehler": zaehler,
        "fehler_folge": fehler_folge,
        "fehlertext": fehler if fehler else "-",
    }
    if not ok:
        _RESTMIN["quelle"] = {}     # Nachtrag 4: bei einer Stoerung nichts nachfuehren
        if cfg.get("mqtt_ein"):
            # "fahrzeuge" geht NUR aus einem gelungenen Abruf hinaus (C4/V7,
            # Durchgang 02.10.2026). Bis 0.9.26 behauptete ein Neustart waehrend
            # einer Stoerung retained "0 Fahrzeuge" neben stehenden Werten.
            paare = {k: v for k, v in oben.items() if k != "fahrzeuge"}
            # Je Fahrzeug ok=0 und der Zeitpunkt seines letzten Erfolgs (M8) -
            # derselbe Stand wie OK am Endpunkt.
            for nummer, f in fahrzeuge.items():
                paare[f"fahrzeug{nummer}/ok"] = 0
                paare[f"fahrzeug{nummer}/ts"] = f.get("ts")
            versucht, schlecht = mqtt_senden(paare, praefix, retain)
            lox["mqtt_versucht"], lox["mqtt_schlecht"] = versucht, schlecht
            json_schreiben(DATEI_LOXONE, lox, 0o600)
        return lox

    for nummer, f in fahrzeuge.items():
        if not f.get("ok"):
            # Ausgefallenes Fahrzeug: sein Verlauf bekommt keinen Altwert.
            continue
        try:
            verlauf_anhaengen(int(nummer),
                              f.get("soc") if f.get("soc") is not None else f.get("tank_prozent"),
                              f.get("reichweite_km"), f.get("kilometerstand"),
                              cfg["verlauf_tage"])
        except (TypeError, ValueError):
            pass

    if cfg.get("mqtt_ein"):
        paare = dict(oben)
        jetzt_r = time.time()
        quelle: dict = {}
        for nummer, f in fahrzeuge.items():
            if not f.get("ok"):
                # AUSGEFALLENES FAHRZEUG (C5/M8, Durchgang 02.10.2026;
                # Entscheidung 8): seine Zustaende bleiben retained im Broker
                # stehen, gesendet werden nur ok=0, der Zeitpunkt des letzten
                # Erfolgs und der Ausfalltext. Seine Werte erneut zu senden,
                # liesse alte Messwerte frisch aussehen.
                paare[f"fahrzeug{nummer}/ok"] = 0
                paare[f"fahrzeug{nummer}/ts"] = f.get("ts")
                if str(f.get("ausfalltext") or "").strip():
                    paare[f"fahrzeug{nummer}/ausfalltext"] = f.get("ausfalltext")
                continue
            ausfaelle = f.get("ausfaelle") or {}

            def strich(feld, w, _aus=ausfaelle):
                # M1/M2: ein gelungener Abruf ohne Aussage -> "-" (nur fuer
                # behaltene Zustaende, und nur wenn der Abschnitt geantwortet hat).
                if feld in MQTT_STRICH_ABSCHNITT and MQTT_STRICH_ABSCHNITT[feld] not in _aus \
                        and not mqtt_ohne_retain(feld):
                    return "-"
                return w

            for feld in MQTT_FELDER:
                if feld in RESTMIN_THEMEN:
                    # Nachtrag 4: Restminuten aus der Endzeit (nicht im Abbild).
                    ende = f.get(RESTMIN_THEMEN[feld])
                    w = None if ende is None else restminuten(ende, jetzt_r)
                    if ende is not None:
                        quelle.setdefault(str(nummer), {})[feld] = ende
                else:
                    w = g41_wert(nummer, feld, f)
                if w is None:
                    # Zustand -> "-" (M1/M2); Messwert -> -1-Regel (Nachtrag 4); sonst nicht gesendet.
                    w = strich(feld, w)
                    if w is None:
                        w = mqtt_messwert(feld, None, ausfaelle)
                paare[f"fahrzeug{nummer}/{feld}"] = w
            for feld in MQTT_TEXTFELDER:
                w = f.get(feld)
                # Ein leeres Textfeld geht NIE leer hinaus: mit Retain loeschte
                # eine leere Nutzlast das behaltene Thema. Ohne Aussage nach
                # einem gelungenen Abruf wird es "-" (M1), sonst bleibt es weg.
                if w is None or str(w).strip() == "":
                    w = strich(feld, None)
                    if w is None:
                        continue
                paare[f"fahrzeug{nummer}/{feld}"] = w
        # AUS DEM KONTO ENTFERNT (M5, Durchgang 02.10.2026; Entscheidungen 8
        # und 16): einmal "-" auf alle behaltenen Themen der Nummer. Bis 0.9.26
        # standen 49 retained Themen eines verkauften Autos fuer immer im
        # Broker (MQTT-Bericht V5). Die Nummer bleibt vergeben.
        for nummer in entfernt:
            for feld in MQTT_FELDER + MQTT_TEXTFELDER:
                if not mqtt_ohne_retain(feld):
                    paare[f"fahrzeug{nummer}/{feld}"] = "-"
            _LOG.info("MQTT: Fahrzeug %s fuehrt das Konto nicht mehr - seine behaltenen "
                      "Themen bekommen einmal '-'.", nummer)
        _RESTMIN["quelle"] = quelle
        _RESTMIN["gesendet"] = {k: v for k, v in paare.items()
                                if k.rsplit("/", 1)[-1] in RESTMIN_THEMEN}
        versucht, schlecht = mqtt_senden(paare, praefix, retain)
        lox["mqtt_versucht"], lox["mqtt_schlecht"] = versucht, schlecht
        json_schreiben(DATEI_LOXONE, lox, 0o600)

    return lox


# ---------------------------------------------------------------------------
# Horcher auf fremde MQTT-Themen
#
# Zwei Verwendungen, beide freiwillig und ab Werk aus:
#
#   Ladeempfehlung     ein Thema mit einem Zahlenwert (Strompreis,
#                      PV-Ueberschuss) wird gegen eine Grenze gehalten. Das
#                      Ergebnis ist eine 1/0-EMPFEHLUNG als eigenes Feld - das
#                      Plugin entscheidet nicht, es empfiehlt. Wer daraus eine
#                      Ladefreigabe macht, tut das in Loxone und sieht es dort.
#   Abfahrtszeit       das Thema des Abfahrts-Assistenten. Faellt die
#                      Restzeit unter den eingestellten Vorlauf, wird die
#                      Klimatisierung EINMAL je Abfahrt angefordert.
#
# paho-mqtt ist eine zusaetzliche Abhaengigkeit. Fehlt sie, laeuft alles
# uebrige weiter und der Selbsttest SAGT es - ein Bedienelement, dessen Wert
# nirgends ankommt, ist schlimmer als ein fehlendes.
# ---------------------------------------------------------------------------
# Die Rueckgabecodes des MQTT-CONNACK im Klartext. Code 5 ist der Fall,
# den diese Anlage schon einmal hatte: der Broker lief, der Miniserver
# meldete "Authentifizierung fehlgeschlagen", und niemand sah den Grund.
_CONNACK = {
    1: "Der Broker lehnt die Protokollfassung ab.",
    2: "Der Broker lehnt die Kennung des Clients ab.",
    3: "Der Broker ist nicht verfuegbar.",
    4: "Benutzername oder Passwort des Brokers sind falsch. Sie stehen unter "
       "System -> MQTT Gateway (Brokeruser, Brokerpass).",
    5: "Der Broker hat die Anmeldung abgewiesen (nicht autorisiert). Traegt "
       "System -> MQTT Gateway einen Benutzer und ein Passwort? Ohne sie "
       "kommt der Horcher nicht hinein.",
}
# DIESELBEN Faelle unter paho 2.x. Dort kommen die Ursachencodes aus MQTT 5
# (132-136) statt der CONNACK-Codes aus MQTT 3.1.1 (1-5). Die paho-Fassung
# haengt an der virtuellen Umgebung dieses Plugins; am LoxBerry installiert
# postinstall.sh 2.1.0 (gemessen 17.09.2026: `pip list` im venv). Bis 0.9.19
# kannte diese Tabelle nur 1-5, und eine abgewiesene Anmeldung erschien als
# "Der Broker hat die Anmeldung mit Code 135 abgewiesen." - ausgerechnet bei
# dem Fall, fuer den der Klartext geschrieben ist.
for _alt, _neu in ((1, 132), (2, 133), (3, 136), (4, 134), (5, 135)):
    _CONNACK[_neu] = _CONNACK[_alt]


class Horcher:
    """Haelt eine MQTT-Verbindung und merkt sich die letzten Werte."""

    def __init__(self):
        self.werte: dict[str, str] = {}
        # Empfangszeit je Thema (M4, Durchgang 02.10.2026): ein Preis, der
        # nicht mehr kommt, ist keine Grundlage fuer eine Empfehlung.
        self.zeiten: dict[str, float] = {}
        # Gruppe 41: haus/<quelle>/... mit Retain-Bit; Frische nur ueber den live gesehenen Zaehler.
        self.haus: dict = {}
        self.klient = None
        self.themen: tuple = ()
        self.grund = ""
        self.verbunden = False

    def moeglich(self) -> tuple[bool, str]:
        try:
            import paho.mqtt.client  # noqa: F401
            return (True, "")
        except ImportError:
            return (False, "Die Bibliothek paho-mqtt fehlt in der virtuellen Umgebung. "
                           "Ohne sie gibt es weder Ladeempfehlung noch Vorklimatisierung; "
                           "alles uebrige arbeitet unveraendert.")

    def gewuenscht(self, cfg: dict) -> tuple:
        t = []
        if cfg.get("empf_thema") and cfg.get("ladeempf_quelle") != "haus":
            t.append(str(cfg["empf_thema"]))
        if cfg.get("abfahrt_ein") and cfg.get("abfahrt_thema"):
            t.append(str(cfg["abfahrt_thema"]))
        # Gruppe 41: Haus-Themen - dieselbe Bedingung wie vw_haus_quellen() in vw_lib.php.
        t += haus_themen(cfg)
        return tuple(sorted(set(t)))

    def pflegen(self, cfg: dict) -> None:
        """Verbindung auf- oder abbauen, je nach Konfiguration.

        Wird bei jedem Takt gerufen. Aendern sich die Themen, wird neu
        abonniert - Einstellungen sollen ohne Neustart wirken.
        """
        soll = self.gewuenscht(cfg)
        if not soll:
            self.schliessen()
            return
        if self.klient is not None and soll == self.themen:
            return
        self.schliessen()
        ok, grund = self.moeglich()
        if not ok:
            self.grund = grund
            melde_gebremst("horcher_paho", grund, 86400)
            return
        z = mqtt_zustand()
        broker = z.get("broker") or "127.0.0.1"
        try:
            port = int(z.get("brokerport") or 1883)
        except (TypeError, ValueError):
            port = 1883
        try:
            import paho.mqtt.client as mqtt

            def bei_nachricht(_klient, _daten, nachricht):
                try:
                    wert = nachricht.payload.decode("utf-8", "replace")
                    retained = bool(getattr(nachricht, "retain", False))
                    # Gruppe 41 (B3): Haus-Themen (Regeln/07 Nr. 15) werden auch retained angenommen - sie
                    # gelten aber erst mit einem LIVE gesehenen Zaehlerwechsel ihrer Quelle.
                    if str(nachricht.topic).startswith("haus/"):
                        haus_merken(self.haus, nachricht.topic, wert.strip(), retained, time.time())
                        return
                    self.werte[nachricht.topic] = wert
                    # B3 (Gruppe 41, Regeln/07 :221): eine retained Nachricht kommt beim Abonnieren mit
                    # beliebigem Alter an. Bis 0.9.32 galt sie als frisch zum Empfangszeitpunkt. Seit 0.9.33
                    # hat sie kein Alter (0 = nie frisch); erst eine live empfangene Nachricht zaehlt.
                    self.zeiten[nachricht.topic] = 0.0 if retained else time.time()
                except Exception:  # noqa: BLE001 - eine unlesbare Nutzlast ist kein Wert
                    pass

            def bei_verbindung(klient, _daten, _flags, *rest):
                # Der Rueckgabecode ENTSCHEIDET. Bis 0.9.11 verschwand er in
                # *_rest und self.verbunden wurde bedingungslos wahr gesetzt:
                # paho ruft on_connect auch bei einem CONNACK mit rc != 0
                # (falsches Kennwort, "not authorised"), subscribe() lieferte
                # dann stillschweigend MQTT_ERR_NO_CONN, und die Oberflaeche
                # meldete dauerhaft "Horcher verbunden", waehrend kein
                # einziger Wert ankam. Das ist die Lage aus den Hausregeln:
                # "erreichbar" ist nicht "angemeldet".
                #
                # rc kommt als erstes Element von rest. Unter paho 2.x ist es
                # ein ReasonCode-Objekt, unter 1.x eine Zahl; beide lassen
                # sich auf eine Zahl bringen, und beide melden 0 fuer Erfolg.
                code = 0
                if rest:
                    try:
                        code = int(getattr(rest[0], "value", rest[0]) or 0)
                    except (TypeError, ValueError):
                        code = 0
                if code != 0:
                    self.verbunden = False
                    self.grund = _CONNACK.get(
                        code, f"Der Broker hat die Anmeldung mit Code {code} abgewiesen.")
                    melde_gebremst("horcher_connack",
                                   f"MQTT-Horcher: {self.grund}", 3600)
                    return
                self.verbunden = True
                self.grund = ""
                for th in soll:
                    try:
                        klient.subscribe(th)
                    except Exception:  # noqa: BLE001
                        pass

            def bei_trennung(*_a):
                self.verbunden = False

            # Der Aufruf unterscheidet sich zwischen paho 1.x und 2.x. Die
            # Fassung wird nicht geraten, sondern abgefragt.
            # Die Fassung wird abgetastet, nicht angenommen: paho-mqtt 2.x schreibt
            # bei VERSION1 eine DeprecationWarning in JEDES Protokoll (am Geraet an
            # 2.1.0 gemessen, 06.09.2026), paho 1.x kennt die Aufzaehlung gar nicht.
            try:
                k = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)   # paho 2.x
            except (AttributeError, TypeError):
                try:
                    k = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
                except (AttributeError, TypeError):
                    k = mqtt.Client()                                # paho 1.x
            k.on_message = bei_nachricht
            k.on_connect = bei_verbindung
            k.on_disconnect = bei_trennung
            if z.get("benutzer"):
                k.username_pw_set(z["benutzer"], z.get("passwort") or None)
            k.connect_async(broker, port, 60)
            k.loop_start()
            self.klient = k
            self.themen = soll
            self.grund = ""
            _LOG.info("MQTT-Horcher gestartet: %s:%s, Themen %s", broker, port, ", ".join(soll))
        except Exception as err:  # noqa: BLE001
            self.grund = fehlertext(err)
            melde_gebremst("horcher_start", f"MQTT-Horcher nicht moeglich: {self.grund}", 3600)

    def schliessen(self) -> None:
        if self.klient is not None:
            try:
                self.klient.loop_stop()
                self.klient.disconnect()
            except Exception:  # noqa: BLE001
                pass
        self.klient = None
        self.themen = ()
        self.verbunden = False
        self.haus = {}

    def zahl(self, thema: str):
        return komma(self.werte.get(thema))


_HORCHER = Horcher()


# ---------------------------------------------------------------------------
# Gruppe 41 (0.9.33): Hausform im Dienst - Haus-Themen (Regeln/07 Nr. 15),
# Vorklimatisierung nach MG-Regeln, Ankunft, Ladekosten, Ladeempfehlung aus
# haus/strompreis, Meldungen an SignalBot. Bauvorschrift
# Pruefung-Durchgang-2026-09-29/wz_bild/GRUPPE41_BAUVORSCHRIFT.md, G4-G8 und 2.3.
# Die reinen Funktionen sind wortgleich aus AudiConnect 0.9.30/Skoda-Connect-NG
# 0.9.34 uebernommen (stueck_dienst_bauen.py); hier die Rahmen fuer VW:
# Horcher mit werte/zeiten, befehl_ausfuehren(liste, cfg, b), Ladeprotokoll in
# fortschreiben(), Merker in vorklima.json (B2) wie Skoda.
# ---------------------------------------------------------------------------
HAUS_FRIST = 120                 # Nr. 15: ein Wert gilt, solange der Zaehler der Quelle binnen 120 s live wechselt
VORKLIMA_VERSUCHE = 3            # 1 senden, 2 Abruf statt senden, 3 senden - hoechstens zwei "Klima ein"
VORKLIMA_ABSTAND = 120           # Sekunden zwischen zwei Versuchen
VORKLIMA_FAHRZEUG_FRISCH = 3600  # Aussentemperatur des Fahrzeugs gilt aus einem Abbild bis 60 min
AUSSEN_LOXONE_FRISCH = 1800      # G39 E-E4: Aussentemperatur aus Loxone gilt 30 min
ANKUNFT_ABSTAND = 1800           # hoechstens ein Abruf je 30 min wegen einer Ankunft
ANKUNFT_STAND_ALT = 300          # nur, wenn der Fahrzeugstand aelter als 5 min ist
ABFAHRT_THEMA_FRISCH = 300       # B4: das bisherige Thema gilt nur, wenn es binnen 300 s LIVE kam (wie Audi)
DATEI_AUSSEN = PDATA / "aussentemp.json"            # schreibt die Bruecke (vw_aussentemp_merken())
DATEI_VORKLIMA = PDATA / "vorklima.json"            # B2: Merker der Vorklimatisierung und der Ankunft
DATEI_LADEKOSTEN = PDATA / "ladekosten.json"        # Kosten und Preis der letzten Ladung je Fahrzeug
DATEI_MITSCHRIFT = PDATA / "ladung_mitschrift.json" # offene Mitschrift - uebersteht einen Neustart
SIGNAL_TEST_WARTEN = 5           # Sekunden, die --signal-test auf die Quittung der SignalBot-Bruecke wartet
_BROKER_SENDEN = None            # Nahtstelle fuer Proben: Funktion (thema, nutzlast) -> bool
# Die drei neuen Fahrzeugthemen kommen nicht aus dem Abbild (loxone.json), sondern von hier;
# "mitschrift" sind die offenen Ladungen zu Hause (Fahrzeugnummer -> Eintrag).
_G41 = {"vorklima": {}, "ladung": {}, "mitschrift": {}}


def zahl(wert, nachkomma=None):
    """Kommazahl (komma()), wahlweise gerundet; leer/kaputt -> None. Fuer die uebernommenen Funktionen."""
    z = komma(wert)
    return z if z is None or nachkomma is None else round(z, nachkomma)


def g41_wert(nummer, feld: str, f: dict):
    """Wert eines Fahrzeugthemas fuer MQTT: vorklima, ladung_kosten, ladung_preis aus _G41, sonst aus dem Abbild."""
    nr = str(nummer)
    if feld == "vorklima":
        return _G41["vorklima"].get(nr, 0)
    if feld in ("ladung_kosten", "ladung_preis"):
        e = _G41["ladung"].get(nr) or {}
        w = e.get(feld[len("ladung_"):])
        return -1 if w is None else w
    return f.get(feld)


def haus_quelle(thema: str) -> str:
    """'haus/abfahrt/in_min' -> 'abfahrt'; haus/tag/<n>/da gehoert zum Anbieter der Anwesenheit (BLE-Scanner)."""
    teile = str(thema).split("/")
    if len(teile) < 3 or teile[0] != "haus":
        return ""
    return "anwesenheit" if teile[1] == "tag" else teile[1]


def haus_merken(stand: dict, thema: str, wert: str, retained: bool, jetzt: float) -> None:
    """Eine Nachricht unter haus/ einarbeiten (rein bis auf stand).

    B3 (Gruppe 41): ein Wert wird angenommen, auch retained - er gilt aber erst, wenn
    der Zaehler seiner Quelle LIVE wechselt. Ein retained Zaehler zaehlt nie; ein live
    gesehener Zaehler mit neuem Wert setzt 'seit' (Form mg_haus_merken())."""
    q = haus_quelle(thema)
    if not q:
        return
    e = stand.setdefault(q, {"werte": {}, "zaehler": None, "seit": 0})
    if str(thema).endswith("/zaehler"):
        if not retained and str(wert) != str(e.get("zaehler")):
            e["zaehler"] = str(wert)
            e["seit"] = int(jetzt)
    else:
        e.setdefault("werte", {})[thema] = str(wert)


def haus_frisch(stand: dict, q: str, jetzt: float) -> bool:
    """Frisch: 0 <= jetzt - seit <= HAUS_FRIST, seit gesetzt und nicht in der Zukunft (Uhr zurueck -> nicht frisch)."""
    seit = int((stand.get(q) or {}).get("seit") or 0)
    return 0 < seit <= int(jetzt) and int(jetzt) - seit <= HAUS_FRIST


def haus_wert(stand: dict, q: str, thema: str):
    return ((stand.get(q) or {}).get("werte") or {}).get(thema)


def ankunft_themen(quelle) -> list:
    """'tag:<name>,person:<name>' -> Themen; dieselbe Pruefung wie die Oberflaeche (reserviert jemand, zaehler)."""
    q = str(quelle or "").strip()
    if not re.fullmatch(r"(tag|person):[A-Za-z0-9_-]{1,40}(,(tag|person):[A-Za-z0-9_-]{1,40}){0,3}", q):
        return []
    if re.search(r"(^|,)person:(jemand|zaehler)(,|$)", q):
        return []
    aus = []
    for teil in q.split(","):
        art, name = teil.split(":", 1)
        aus.append("haus/tag/%s/da" % name if art == "tag" else "haus/anwesenheit/%s" % name)
    return aus


def haus_themen(cfg: dict) -> list:
    """Die Haus-Themen, die der Dienst abonniert - dieselbe Bedingung wie vw_haus_quellen() in vw_lib.php."""
    t = []
    if cfg.get("abfahrt_ein") and cfg.get("haus_abfahrt_ein"):
        t += ["haus/abfahrt/in_min", "haus/abfahrt/zaehler"]
    anw = False
    if cfg.get("abfahrt_ein") and cfg.get("vorklima_anwesend_ein"):
        t.append("haus/anwesenheit/jemand")
        anw = True
    if cfg.get("ankunft_ein"):
        for x in ankunft_themen(cfg.get("ankunft_quelle")):
            t.append(x)
            anw = True
    if anw:
        t.append("haus/anwesenheit/zaehler")
    if cfg.get("haus_strompreis_ein") or (cfg.get("ladeempf_quelle") == "haus" and cfg.get("empf_grenze") != ""):
        t += ["haus/strompreis/" + x for x in ("vorschau", "ab", "raster", "aktuell", "zaehler")]
    return t


def haus_zustand(stand: dict, jetzt: float) -> dict:
    """Fuer zustand.json (Reiter Test): je Quelle frisch, Alter, seit - nie Werte."""
    aus = {}
    for q, e in (stand or {}).items():
        seit = int((e or {}).get("seit") or 0)
        aus[q] = {"frisch": 1 if haus_frisch(stand, q, jetzt) else 0, "seit": seit,
                  "alter": int(jetzt) - seit if seit else -1}
    return aus


# ---- G5: Vorklimatisierung nach MG-Regeln (B1, B2, B4, B5) ----
def vorklima_kennung(jetzt: float, in_min: int) -> int:
    """Kennung einer Abfahrt: Abfahrtszeit auf 5 min gerundet (wie PHP round, halbe nach oben)."""
    return int(math.floor((int(jetzt) + int(in_min) * 60) / 300.0 + 0.5) * 300)


def vorklima_neu(k: int) -> dict:
    return {"kennung": int(k), "versuche": 0, "letzter": 0, "erfolg": 0, "abgerufen": 0,
            "aufgegeben": 0, "mild": 0}


def vorklima_schritt(m: dict, in_min, jetzt: float, vorlauf: int, letztes_ok=None):
    """Ein Schritt (rein; Abschrift mg_vorklima_schritt()). Rueckgabe (aktion, neuer_merker),
    aktion nichts | senden | abruf | aufgeben | erledigt.
    - Ausloesen nur bei 1 <= in_min <= vorlauf (0 = ueberfaellig und -1 nie).
    - Eine Kennung, die hoechstens 5 min von der gemerkten abweicht, ist dieselbe Abfahrt.
    - Hoechstens VORKLIMA_VERSUCHE Versuche, Abstand >= VORKLIMA_ABSTAND: 1 senden, 2 Abruf
      (gesendet wird in diesem Schritt nicht), 3 senden; danach aufgeben. Hoechstens zwei "Klima ein".
    - B1: erledigt erst nach einem Erfolg (letztes_ok is True), nie vorher."""
    m = dict(m or {})
    jetzt = int(jetzt)
    if letztes_ok is True and "kennung" in m:
        m["erfolg"] = 1
    try:
        n = int(str(in_min).strip())
    except (TypeError, ValueError):
        return ("nichts", m)
    if n < 1 or n > max(1, int(vorlauf)):
        return ("nichts", m)
    k = vorklima_kennung(jetzt, n)
    if "kennung" not in m or abs(k - int(m["kennung"])) > 300:
        m = vorklima_neu(k)
    for x, v in vorklima_neu(k).items():
        m.setdefault(x, v)
    if m["erfolg"] or m["mild"]:
        return ("erledigt", m)
    if m["aufgegeben"]:
        return ("nichts", m)
    if int(m["versuche"]) >= VORKLIMA_VERSUCHE:
        m["aufgegeben"] = 1
        return ("aufgeben", m)
    if int(m["versuche"]) >= 1:
        if jetzt < int(m["letzter"]):
            m["letzter"] = jetzt          # Uhr zurueck: neu zaehlen, nicht senden
            return ("nichts", m)
        if jetzt - int(m["letzter"]) < VORKLIMA_ABSTAND:
            return ("nichts", m)
    m["versuche"] = int(m["versuche"]) + 1
    m["letzter"] = jetzt
    if m["versuche"] == 2 and not m["abgerufen"]:
        m["abgerufen"] = 1
        return ("abruf", m)
    return ("senden", m)


def vorklima_wert(m, jetzt: float) -> int:
    """Thema fahrzeug<n>/vorklima: 0 nichts/aus, 1 gesendet, 2 mild, 3 wartet auf Wiederholung,
    4 wartet auf Anwesenheit, -1 gescheitert (rein, wie mg_vorklima_wert())."""
    if not isinstance(m, dict) or "kennung" not in m:
        return 0
    if int(m["kennung"]) + 1800 < int(jetzt):
        return 0
    if m.get("aufgegeben"):
        return -1
    if m.get("erfolg"):
        return 1
    if m.get("mild"):
        return 2
    if m.get("warten_anwesend"):
        return 4
    if m.get("versuche"):
        return 3
    return 0


def vorklima_wetter(cfg: dict, aussen_lox, alter_lox, aussen_fz, alter_fz) -> str:
    """klimatisieren | mild | ohne_aussage (rein). Zuerst Loxone (<= AUSSEN_LOXONE_FRISCH s), sonst das
    Fahrzeug (Abbild <= VORKLIMA_FAHRZEUG_FRISCH s), sonst ohne_aussage - dann wird klimatisiert
    (nie "nicht klimatisiert" ohne Aussage). Haken aus -> klimatisieren."""
    if not cfg.get("vorklima_wetter_ein"):
        return "klimatisieren"
    wert = None
    for w, a, grenze in ((aussen_lox, alter_lox, AUSSEN_LOXONE_FRISCH),
                         (aussen_fz, alter_fz, VORKLIMA_FAHRZEUG_FRISCH)):
        z, al = zahl(w, 1), zahl(a)
        if z is not None and z > -99 and al is not None and 0 <= al <= grenze:
            wert = z
            break
    if wert is None:
        return "ohne_aussage"
    kalt = zahl(cfg.get("vorklima_kalt_unter"), 1)
    heiss = zahl(cfg.get("vorklima_heiss_ueber"), 1)
    kalt = 5.0 if kalt is None else kalt
    heiss = 25.0 if heiss is None else heiss
    return "mild" if kalt <= wert <= heiss else "klimatisieren"


def aussentemp_lesen(jetzt: float):
    """Die Aussentemperatur aus Loxone (Bruecke): (wert, alter_s) oder (None, -1)."""
    d = json_lesen(DATEI_AUSSEN)
    w, t = zahl(d.get("wert"), 1), zahl(d.get("t"))
    if w is None or t is None or t > jetzt:
        return (None, -1)
    return (w, int(jetzt - t))


def vorklima_minuten(horcher, cfg: dict, jetzt: float):
    """(in_min oder None, Herkunft). Haus-Thema bei frischer Quelle; sonst das bisherige freie Thema als
    Rueckfall (E6, entfaellt mit der naechsten Fassung) - B3/B4: nur ein LIVE empfangener Wert, hoechstens
    ABFAHRT_THEMA_FRISCH s alt (ein retained Wert gilt nie als frisch; bis 0.9.32 ohne jede Altersgrenze)."""
    if cfg.get("haus_abfahrt_ein") and haus_frisch(horcher.haus, "abfahrt", jetzt):
        v = str(haus_wert(horcher.haus, "abfahrt", "haus/abfahrt/in_min") or "").strip()
        try:
            return (int(float(v)), "haus")
        except ValueError:
            return (-1, "haus")            # '-' oder leer: keine Abfahrt bekannt
    thema = str(cfg.get("abfahrt_thema") or "")
    if not thema:
        return (None, "aus")
    roh = horcher.werte.get(thema)
    seit = jetzt - float(horcher.zeiten.get(thema, 0.0) or 0.0)
    if roh is None or not horcher.zeiten.get(thema) or seit < 0 or seit > ABFAHRT_THEMA_FRISCH:
        return (None, "still")
    z = zahl(roh)
    return (None, "thema") if z is None else (int(z), "thema")


def vorklima_takt(horcher, cfg: dict, merker: dict, stand: dict, jetzt: float):
    """Ein Schritt im Wartetakt. Rueckgabe: Auftrag fuer befehl_ausfuehren() oder None."""
    if not cfg.get("abfahrt_ein"):
        return None
    nr = str(max(1, ganz(cfg.get("abfahrt_fahrzeug"), 1)))
    vk = merker.setdefault("vorklima", {})
    m = dict(vk.get(nr) or {})
    in_min, herkunft = vorklima_minuten(horcher, cfg, jetzt)
    if in_min is None:
        if herkunft == "still" and cfg.get("haus_abfahrt_ein"):
            melde_gebremst("abfahrt_still",
                           "Vorklimatisierung: es kommt keine frische Abfahrtszeit an (haus/abfahrt schweigt "
                           "und das bisherige Thema ist still).", 3600)
        return None
    vorlauf = ganz(cfg.get("abfahrt_vorlauf"), 20)
    if not 1 <= in_min <= vorlauf:
        aktion, m = vorklima_schritt(m, in_min, jetzt, vorlauf, None)
        vk[nr] = m
        return None
    if not cfg.get("steuerung_ein"):
        melde_gebremst("vorklima_steuerung", "Vorklimatisierung: die Abfahrt liegt im Vorlauf, aber "
                       "'Schreibende Befehle zulassen' ist aus - es wird nichts gesendet.", 3600)
        return None
    # B5: das eingestellte Fahrzeug, nie still ein anderes. Fehlt es im Konto, kein Befehl.
    fz = stand.get("fahrzeuge") or {}
    if nr not in fz:
        melde_gebremst("vorklima_fz_%s" % nr, "Vorklimatisierung: Fahrzeug %s gibt es im Konto nicht "
                       "(Reiter Einstellungen, 'Fahrzeug fuer die Vorklimatisierung') - es wird nichts "
                       "gesendet." % nr, 3600)
        return None
    k = vorklima_kennung(jetzt, in_min)
    if "kennung" not in m or abs(k - int(m["kennung"])) > 300:
        m = vorklima_neu(k)
    # E7a: live 0 an haus/anwesenheit/jemand -> warten (kein Versuch verbraucht).
    if (cfg.get("vorklima_anwesend_ein") and haus_frisch(horcher.haus, "anwesenheit", jetzt)
            and str(haus_wert(horcher.haus, "anwesenheit", "haus/anwesenheit/jemand")).strip() == "0"
            and not m.get("erfolg") and not m.get("mild") and not m.get("aufgegeben")):
        if not m.get("warten_anwesend"):
            _LOG.info("Vorklimatisierung Fahrzeug %s: niemand zu Hause - sie wartet.", nr)
        m["warten_anwesend"] = 1
        vk[nr] = m
        return None
    m.pop("warten_anwesend", None)
    aktion, m = vorklima_schritt(m, in_min, jetzt, vorlauf, None)
    if aktion in ("senden", "abruf") and int(m.get("versuche", 0)) == 1 and not m.get("wetter"):
        m["wetter"] = 1
        aw, al = aussentemp_lesen(jetzt)
        f = fz.get(nr) or {}
        fz_alter = int(jetzt - ganz(f.get("ts"), 0)) if ganz(f.get("ts"), 0) > 0 else -1
        w = vorklima_wetter(cfg, aw, al, f.get("aussentemperatur"), fz_alter)
        if w == "mild":
            m.update({"mild": 1, "versuche": 0, "letzter": 0})
            vk[nr] = m
            lox = aw is not None and 0 <= al <= AUSSEN_LOXONE_FRISCH
            _LOG.info("Vorklimatisierung Fahrzeug %s: mild (%s Grad, Quelle %s) - keine Klimatisierung.",
                      nr, aw if lox else f.get("aussentemperatur"), "Loxone" if lox else "Fahrzeug")
            return None
    vk[nr] = m
    if aktion == "senden":
        _LOG.info("Vorklimatisierung Fahrzeug %s: Abfahrt in %s min (Quelle %s), Versuch %s, Zieltemperatur %s Grad.",
                  nr, in_min, herkunft, m["versuche"], cfg.get("abfahrt_temp"))
        return {"aktion": "klima_start", "fahrzeug": nr, "temp": cfg.get("abfahrt_temp", 21), "von": "vorklima"}
    if aktion == "abruf":
        _LOG.info("Vorklimatisierung Fahrzeug %s: Versuch 2 ist ein Abruf (laeuft die Klimatisierung schon?).", nr)
        return {"aktion": "abruf", "von": "vorklima"}
    if aktion == "aufgeben":
        text = "Volkswagen ID: die Vorklimatisierung fuer Fahrzeug %s ist gescheitert (zwei Versuche)." % nr
        melde_gebremst("vorklima_%s" % m.get("kennung"), text, 86400)
        signal_senden(cfg, "vorklima", nr, text)
    return None


def vorklima_ergebnis(merker: dict, auftrag: dict, ok) -> None:
    """B1: der Merker gilt als erledigt erst nach ok 1 von befehl_ausfuehren()."""
    if auftrag.get("aktion") != "klima_start":
        return
    m = (merker.get("vorklima") or {}).get(str(auftrag.get("fahrzeug")))
    if isinstance(m, dict) and ok == 1:
        m["erfolg"] = 1


def vorklima_nach_abruf(merker: dict, cfg: dict, fahrzeuge: dict) -> None:
    """Ein Abruf, der 'Klimatisierung laeuft' zeigt, erledigt die laufende Abfahrt."""
    nr = str(max(1, ganz(cfg.get("abfahrt_fahrzeug"), 1)))
    m = (merker.get("vorklima") or {}).get(nr)
    f = fahrzeuge.get(nr) or {}
    if isinstance(m, dict) and m.get("versuche") and not m.get("erfolg") and f.get("klima_an") == 1:
        m["erfolg"] = 1
        _LOG.info("Vorklimatisierung Fahrzeug %s: der Abruf zeigt, die Klimatisierung laeuft.", nr)


def vorklima_melden(cfg: dict, merker: dict, jetzt: float) -> None:
    """fahrzeug<n>/vorklima bei jedem Wechsel sofort senden (nicht erst mit dem naechsten Abruf), nie retained."""
    nr = str(max(1, ganz(cfg.get("abfahrt_fahrzeug"), 1)))
    w = vorklima_wert((merker.get("vorklima") or {}).get(nr), jetzt) if cfg.get("abfahrt_ein") else 0
    alt = dict(_G41["vorklima"])
    _G41["vorklima"] = {nr: w}
    if cfg.get("mqtt_ein") and alt.get(nr, 0) != w:
        praefix = str(cfg.get("mqtt_topic") or "volkswagen").strip("/") or "volkswagen"
        mqtt_senden({"fahrzeug%s/vorklima" % nr: w}, praefix, 0)


def vorklima_merker_lesen() -> dict:
    """B2: der Merker liegt in einer Datei und uebersteht Neustart und Update (preupgrade.sh rettet data/)."""
    d = json_lesen(DATEI_VORKLIMA)
    return d if isinstance(d, dict) else {}


# ---- G4/E7b: Abruf bei Ankunft ----
def ankunft_flanke(alt, wert, frisch: bool) -> dict:
    """Live-Flanke 0 -> 1 zaehlen (rein, wie mg_ankunft_flanke())."""
    wert = "" if wert is None else str(wert).strip()
    if not isinstance(alt, dict) or "wert" not in alt:
        return {"wert": wert, "frisch": bool(frisch), "nr": 0}
    nr = int(alt.get("nr") or 0)
    if alt.get("frisch") and frisch and str(alt.get("wert")) == "0" and wert == "1":
        nr += 1
    return {"wert": wert, "frisch": bool(frisch), "nr": nr}


def ankunft_entscheiden(flanke_nr, ausgefuehrt, letzte_abfrage, jetzt, stand_alter):
    """(aktion, neue_ausgefuehrt) mit aktion nichts | abfragen | gedrosselt | frisch (rein, wie
    mg_ankunft_entscheiden(); Alter in Sekunden). Jede Flanke genau einmal."""
    if int(flanke_nr) <= int(ausgefuehrt):
        return ("nichts", int(ausgefuehrt))
    neu = int(flanke_nr)
    if int(letzte_abfrage) > 0 and int(jetzt) >= int(letzte_abfrage) \
            and int(jetzt) - int(letzte_abfrage) < ANKUNFT_ABSTAND:
        return ("gedrosselt", neu)
    if stand_alter is not None and 0 <= int(stand_alter) <= ANKUNFT_STAND_ALT:
        return ("frisch", neu)
    return ("abfragen", neu)


def ankunft_takt(horcher, cfg: dict, merker: dict, stand: dict, jetzt: float) -> bool:
    """True, wenn wegen einer Ankunft abgefragt werden soll. Der Sofortabruf ist kontoweit (ein Durchgang
    fuer alle Fahrzeuge) - deshalb eine Quelle je Konto, wie bei Audi."""
    if not cfg.get("ankunft_ein"):
        return False
    a = merker.setdefault("ankunft", {})
    fl = a.setdefault("flanken", {})
    frisch = haus_frisch(horcher.haus, "anwesenheit", jetzt)
    soll = False
    stand_alter = int(jetzt - ganz(stand.get("ts"), 0)) if ganz(stand.get("ts"), 0) > 0 else None
    for t in ankunft_themen(cfg.get("ankunft_quelle")):
        fl[t] = ankunft_flanke(fl.get(t), haus_wert(horcher.haus, "anwesenheit", t), frisch)
        aktion, neu = ankunft_entscheiden(fl[t]["nr"], int((a.get("ausgefuehrt") or {}).get(t, 0)),
                                          int(a.get("letzte") or 0), jetzt, stand_alter)
        if aktion != "nichts":
            a.setdefault("ausgefuehrt", {})[t] = neu
            _LOG.info("Ankunft (%s): %s.", t, {"abfragen": "Abruf", "gedrosselt": "kein Abruf - der letzte "
                      "liegt keine 30 Minuten zurueck", "frisch": "kein Abruf - der Stand ist juenger als "
                      "5 Minuten"}[aktion])
        if aktion == "abfragen":
            soll = True
    if soll:
        a["letzte"] = int(jetzt)
    return soll


# --- LADEKOSTEN-KERN G41 (wortgleich in audi.py, skoda.py, vw.py) ---
LADUNG_TAKT_S = 60          # eine Probe je Minute; die erste steht fuer 60 s
LADUNG_LUECKE_S = 120       # ein groesserer Abstand ist eine Luecke ohne Energie
LADUNG_ABDECKUNG = 0.9      # fehlt der Preis fuer mehr als 10 % der Energie: Kosten -1, nie geschaetzt


def _ladung_zahl(w):
    if w is None or isinstance(w, bool):
        return None
    try:
        return float(str(w).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def preis_zum_zeitpunkt(vorschau, ab, raster, aktuell, frisch, t):
    """Endpreis in ct/kWh zum Zeitpunkt t (rein, wie mg_preis_zum_zeitpunkt()): vorschau[(t - ab) // raster]
    bei Raster 900/3600 und ab im Raster; sonst aktuell, aber nur bei frischer Quelle; sonst None."""
    import re
    v = vorschau.strip() if isinstance(vorschau, str) else ""
    if v and len(v) <= 4096 and re.fullmatch(r"-?\d{1,4}(\.\d{1,3})?(;-?\d{1,4}(\.\d{1,3})?)*", v):
        a, r = _ladung_zahl(ab), _ladung_zahl(raster)
        if a is not None and r is not None and int(r) in (900, 3600) and int(a) > 0 \
                and int(a) % int(r) == 0 and int(t) >= int(a):
            liste = v.split(";")
            i = (int(t) - int(a)) // int(r)
            if i < len(liste):
                return float(liste[i])
    if frisch:
        return _ladung_zahl(aktuell)
    return None


def ladung_kosten(proben, kwh_gemeldet=None):
    """Kosten einer Ladung aus Minutenproben [t, kw, preis] (rein).

    Jede Probe steht fuer die Zeit seit der vorigen (die erste fuer LADUNG_TAKT_S); ein Abstand ueber
    LADUNG_LUECKE_S ist eine Luecke. Gewicht = gemeldete Ladeleistung x Dauer; meldet keine Probe eine
    Leistung, wird nach Dauer verteilt (Renault 2.1.20, Abweichung 4b). Energie = kwh_gemeldet (Akkustand-
    Differenz x Kapazitaet), sonst die Summe der Leistungsgewichte. Abdeckung = Anteil des Gewichts mit
    Preis; unter LADUNG_ABDECKUNG Kosten und Preis -1 (nie geschaetzt).
    Rueckgabe (kwh, kosten_eur, preis_ct, abdeckung, grund)."""
    p = {}
    for x in proben or []:
        try:
            t = int(x[0])
        except (TypeError, ValueError, IndexError):
            continue
        p.setdefault(t, x)
    mit_kw = any((_ladung_zahl(x[1]) or 0) > 0 for x in p.values() if len(x) > 1)
    gesamt = mit = cent = 0.0
    vorher = None
    for t in sorted(p):
        x = p[t]
        dt = LADUNG_TAKT_S if vorher is None else t - vorher
        vorher = t
        if dt <= 0 or dt > LADUNG_LUECKE_S:
            continue
        kw = _ladung_zahl(x[1]) if len(x) > 1 else None
        if mit_kw and (kw is None or kw <= 0):
            continue
        g = (kw if mit_kw else 1.0) * dt / 3600.0
        gesamt += g
        pr = _ladung_zahl(x[2]) if len(x) > 2 else None
        if pr is not None:
            mit += g
            cent += pr * g
    kwh = _ladung_zahl(kwh_gemeldet)
    if kwh is None or kwh <= 0:
        kwh = gesamt if mit_kw else None
    if gesamt <= 0 or kwh is None or kwh <= 0:
        return (-1, -1, -1, 0, "leistung_fehlt")
    abd = mit / gesamt
    if abd < LADUNG_ABDECKUNG:
        return (round(kwh, 3), -1, -1, round(abd, 3), "preis_fehlt")
    preis = cent / mit
    return (round(kwh, 3), round(kwh * preis / 100.0, 2), round(preis, 1), round(abd, 3), "ok")


# Offene Mitschrift ueber einen Neustart retten (Zusatz VW 0.9.33, Hausherr 29.10.; danach wortgleich in
# audi.py und skoda.py zurueckzutragen). Datei je Linie, Inhalt {"fassung": 1, "t": <unix>, "offen":
# {<schluessel>: {..., "start": <unix>, "proben": [[t, kw, preis], ...]}}} - der Schluessel ist die
# Fahrzeugnummer der Linie, der Eintrag gehoert der Linie (nur start und proben prueft der Kern).
LADUNG_MITSCHRIFT_ALTER = 43200   # eine Mitschrift, deren letzte Probe aelter als 12 h ist, wird verworfen


def ladung_mitschrift_speichern(pfad, offen, jetzt) -> bool:
    """Die offenen Mitschriften atomar schreiben (Nebendatei, 0600, os.replace). False bei Fehler."""
    import json as _json
    import os as _os
    tmp = "%s.tmp.%d" % (pfad, _os.getpid())
    try:
        text = _json.dumps({"fassung": 1, "t": int(jetzt), "offen": offen}, ensure_ascii=False, sort_keys=True)
        fd = _os.open(tmp, _os.O_WRONLY | _os.O_CREAT | _os.O_TRUNC, 0o600)
        with _os.fdopen(fd, "w", encoding="utf-8") as h:
            h.write(text)
            h.flush()
            _os.fsync(h.fileno())
        _os.replace(tmp, str(pfad))
        return True
    except (OSError, TypeError, ValueError):
        try:
            _os.unlink(tmp)     # nur die eigene Nebendatei
        except OSError:
            pass
        return False


def ladung_mitschrift_laden(pfad, jetzt):
    """(offen, lage) mit lage keine | ok | teils_verworfen | beschaedigt. Verworfen wird ein Eintrag ohne
    gueltigen Beginn, mit Proben in der Zukunft (> 300 s, Uhr zurueck) oder dessen letzte Zeit aelter als
    LADUNG_MITSCHRIFT_ALTER ist; eine unlesbare Datei gilt als leer (beschaedigt - der Aufrufer meldet es)."""
    import json as _json
    import os as _os
    if not _os.path.exists(str(pfad)):
        return ({}, "keine")
    try:
        with open(str(pfad), encoding="utf-8") as h:
            d = _json.loads(h.read())
        roh = d["offen"]
        if not isinstance(roh, dict) or d.get("fassung") != 1:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        return ({}, "beschaedigt")
    offen, weg = {}, 0
    for k, e in roh.items():
        try:
            start = int(e["start"])
            proben = [p for p in e.get("proben") or [] if isinstance(p, list) and len(p) >= 3]
            zuletzt = max([start] + [int(p[0]) for p in proben])
        except (TypeError, ValueError, KeyError, AttributeError):
            weg += 1
            continue
        if start <= 0 or zuletzt > int(jetzt) + 300 or int(jetzt) - zuletzt > LADUNG_MITSCHRIFT_ALTER:
            weg += 1
            continue
        e = dict(e)
        e["proben"] = proben[-2000:]
        offen[str(k)] = e
    return (offen, "teils_verworfen" if weg else "ok")
# --- ENDE LADEKOSTEN-KERN ---


def ladung_mitschrift_sichern(jetzt: float) -> None:
    """Die offenen Mitschriften in DATEI_MITSCHRIFT (Kern: atomar, 0600)."""
    if not ladung_mitschrift_speichern(DATEI_MITSCHRIFT, _G41["mitschrift"], jetzt):
        melde_gebremst("mitschrift_schreiben", "Ladekosten: die offene Mitschrift liess sich nicht speichern - "
                       "ein Neustart waehrend der Ladung verliert sie.", 3600)


def ladung_mitschrift_holen(jetzt: float) -> None:
    """Beim Dienststart: offene Mitschriften zurueckholen (Kern); beschaedigt oder zu alt -> verworfen, gemeldet."""
    offen, lage = ladung_mitschrift_laden(DATEI_MITSCHRIFT, jetzt)
    _G41["mitschrift"] = offen
    if lage == "beschaedigt":
        melde_gebremst("mitschrift_kaputt", "Ladekosten: die gespeicherte Mitschrift ist beschaedigt und wird "
                       "verworfen - eine laufende Ladung bekommt keine Kosten (-1).", 3600)
    elif lage == "teils_verworfen":
        _LOG.info("Ladekosten: eine gespeicherte Mitschrift war aelter als 12 h oder ungueltig und wurde verworfen.")
    elif offen:
        _LOG.info("Ladekosten: %d offene Mitschrift(en) nach dem Neustart uebernommen.", len(offen))


def ladung_beginnen(nummer, f: dict, cfg: dict, jetzt: float) -> None:
    """Beim Ladebeginn (fortschreiben()): Mitschrift anlegen. 1 zu Hause, 0 unterwegs, -1 ohne Heimzone, -2 aus."""
    z = f.get("zuhause")
    haus = ((1 if z == 1 else (0 if z == 0 else -1)) if cfg.get("haus_strompreis_ein") else -2)
    _G41["mitschrift"][str(nummer)] = {"start": int(jetzt), "haus": haus, "proben": [],
                                       "kw_jetzt": f.get("ladeleistung_kw")}
    ladung_mitschrift_sichern(jetzt)


def ladung_leistung(nummer, f: dict) -> None:
    """Je Abruf waehrend einer Ladung: die zuletzt gemeldete Leistung traegt jede Minutenprobe bis zum naechsten."""
    e = _G41["mitschrift"].get(str(nummer))
    if isinstance(e, dict):
        kw = f.get("ladeleistung_kw")
        e["kw_jetzt"] = float(kw) if isinstance(kw, (int, float)) and not isinstance(kw, bool) else None


def ladung_probe(horcher, cfg: dict, jetzt: float) -> None:
    """Je Minute einer Ladung zu Hause eine Probe [t, kW, Preis] in die offene Mitschrift (und in die Datei)."""
    if not cfg.get("haus_strompreis_ein"):
        return
    h = horcher.haus
    frisch = haus_frisch(h, "strompreis", jetzt)
    neu = False
    for offen in _G41["mitschrift"].values():
        if offen.get("haus") != 1:
            continue
        l = offen.setdefault("proben", [])
        if l and int(jetzt) - int(l[-1][0]) < LADUNG_TAKT_S - 5:
            continue
        preis = preis_zum_zeitpunkt(haus_wert(h, "strompreis", "haus/strompreis/vorschau"),
                                    haus_wert(h, "strompreis", "haus/strompreis/ab"),
                                    haus_wert(h, "strompreis", "haus/strompreis/raster"),
                                    haus_wert(h, "strompreis", "haus/strompreis/aktuell"), frisch, jetzt)
        l.append([int(jetzt), offen.get("kw_jetzt"), preis])
        del l[:-2000]
        neu = True
    if neu:
        ladung_mitschrift_sichern(jetzt)


def ladung_kosten_abschliessen(nummer, kwh, jetzt: float):
    """Beim Ladeende (fortschreiben()): (kosten, preis) fuer CSV und Themen; -1 ohne Aussage, nie geschaetzt.
    Energie = Akkustand-Differenz x Kapazitaet (wie bisher kwh), sonst gemeldete Leistung x Zeit."""
    offen = _G41["mitschrift"].pop(str(nummer), None)
    if offen is None:
        kwh_k, kosten, preis, grund, lh = -1, -1, -1, "mitschrift_fehlt", None
    else:
        lh = offen.get("haus", -2)
        if lh == 1:
            kwh_k, kosten, preis, _abd, grund = ladung_kosten(offen.get("proben") or [], kwh)
        else:
            kwh_k, kosten, preis = -1, -1, -1
            grund = {0: "unterwegs", -1: "heimzone_fehlt"}.get(lh, "aus")
        ladung_mitschrift_sichern(jetzt)
    _G41["ladung"][str(nummer)] = {"kosten": kosten, "preis": preis}
    json_schreiben(DATEI_LADEKOSTEN, _G41["ladung"], 0o600)
    if lh in (0, 1, -1) or grund == "mitschrift_fehlt":
        _LOG.info("Fahrzeug %s: Ladekosten %s EUR, Preis %s ct/kWh, %s kWh (%s).", nummer, kosten, preis, kwh_k, grund)
    return (kosten, preis)


# ---- G8: Meldungen an SignalBot (E9, wie Renault X4) ----
def signal_nutzlast(text: str, dringend: int = 0) -> str:
    """JSON {"text", "dringend"} fuer signalbot/befehl/plugin/<ordner> (rein), hoechstens 4000 Byte."""
    t = str(text or "").replace("\r", " ")
    while True:
        s = json.dumps({"text": t, "dringend": 1 if dringend else 0}, ensure_ascii=False)
        if len(s.encode("utf-8")) <= 4000 or len(t) <= 1:
            return s
        t = t[:max(1, len(t) - max(1, len(s.encode("utf-8")) - 4000) - 2)] + "…"


def signal_thema(praefix, ordner) -> str:
    """'<praefix>/befehl/plugin/<ordner>' oder '' (ungueltig)."""
    p, o = str(praefix or ""), str(ordner or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", p) or not re.fullmatch(r"[a-z0-9_]{1,40}", o):
        return ""
    return "%s/befehl/plugin/%s" % (p, o)


def broker_anmeldung() -> dict:
    """Broker und Anmeldung aus general.json (mqtt_zustand()) - nie in einer Befehlszeile."""
    z = mqtt_zustand()
    return {"host": z.get("broker") or "127.0.0.1", "port": ganz(z.get("brokerport"), 1883) or 1883,
            "auth": ({"username": z["benutzer"], "password": z.get("passwort") or None} if z.get("benutzer") else None)}


def broker_senden(thema: str, nutzlast: str) -> bool:
    """Kurze Sitzung direkt am Broker (Regeln/07 Nr. 15 Technik), qos 0, NIE retained."""
    if _BROKER_SENDEN is not None:
        return bool(_BROKER_SENDEN(thema, nutzlast))
    try:
        import paho.mqtt.publish as pub
    except ImportError:
        melde_gebremst("signal_paho", "SignalBot-Meldung nicht gesendet: paho-mqtt fehlt.", 86400)
        return False
    b = broker_anmeldung()
    try:
        pub.single(thema, nutzlast, qos=0, retain=False, hostname=b["host"], port=b["port"], auth=b["auth"],
                   client_id="volkswagenid-signal-%d" % os.getpid())
        return True
    except Exception as err:  # noqa: BLE001
        melde_gebremst("signal_senden", "SignalBot-Meldung nicht gesendet: %s" % type(err).__name__, 3600)
        return False


SIGNAL_TEXTE = {
    "laden_fertig": "%s: Die Ladung ist beendet.",
    "laden_abbruch": "%s: Die Ladung wurde unter der Ladegrenze oder mit einer Stoerung beendet.",
    "offen": "%s steht offen oder unverriegelt.",
    "ausfall": "Volkswagen ID: seit drei Abrufen keine Daten mehr.",
}


def signal_senden(cfg: dict, anlass: str, nr, text: str) -> bool:
    """Ein Ereignis an SignalBot (ab Werk aus, je Ereignis abwaehlbar, je Ereignis und Fahrzeug 1x je Stunde)."""
    if not cfg.get("signal_ein") or str(cfg.get("signal_" + anlass)).strip() != "1":
        return False
    if _ansage_sperre("signal:%s:%s" % (anlass, nr), int(time.time())):
        return False
    thema = signal_thema(cfg.get("signal_praefix"), PNAME)
    if not thema:
        melde_gebremst("signal_thema", "SignalBot-Meldung nicht gesendet: Praefix oder Ordnername ungueltig.", 86400)
        return False
    ok = broker_senden(thema, signal_nutzlast(text, 0))
    _LOG.info("SignalBot-Meldung %s (Fahrzeug %s): %s.", anlass, nr, "gesendet" if ok else "NICHT gesendet")
    return ok


# ---- G8-Zusatz: Testmeldung an SignalBot (Reiter Einstellungen, vw.py --signal-test) ----
# Uebertragbar auf Audi/Skoda: signal_test_thema(), signal_test_auswerten() und signal_test() brauchen nur
# config(), broker_anmeldung() bzw. die Nahtstelle _SIGNAL_TEST_SITZUNG; der Knopf ruft "<dienst>.py --signal-test".
SIGNAL_TEST_TEXT = "Testmeldung von Volkswagen ID (LoxBerry): der Meldeweg an SignalBot funktioniert."
_SIGNAL_TEST_SITZUNG = None      # Nahtstelle fuer Proben: Funktion (thema, text, warten) -> Quittungstext oder None


def signal_test_thema(praefix) -> str:
    """'<praefix>/befehl/senden' (Befehl 'senden' der SignalBot-Bruecke, Text als Nutzlast) oder '' (rein)."""
    p = str(praefix or "")
    return "%s/befehl/senden" % p if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", p) else ""


def signal_test_auswerten(quittung) -> str:
    """Quittung der Bruecke -> angenommen | abgelehnt | keine_antwort (rein). Die Bruecke 1.0.2 quittiert
    'angenommen' oder 'abgelehnt: <grund>'; alles andere ist keine verwertbare Antwort."""
    if quittung is None or str(quittung).strip() == "":
        return "keine_antwort"
    q = str(quittung).strip().lower()
    if q.startswith("angenommen"):
        return "angenommen"
    if q.startswith("abgelehnt"):
        return "abgelehnt"
    return "keine_antwort"


def _signal_test_sitzung(thema: str, text: str, warten: float):
    """Eine kurze Sitzung: <thema>/quittung abonnieren, Text senden (retain 0), bis 'warten' s auf die Quittung."""
    import paho.mqtt.client as mqtt
    b = broker_anmeldung()
    ergebnis = {"q": None}
    fertig = threading.Event()
    try:
        k = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="volkswagenid-signaltest-%d" % os.getpid())
    except (AttributeError, TypeError):
        k = mqtt.Client(client_id="volkswagenid-signaltest-%d" % os.getpid())
    if b["auth"]:
        k.username_pw_set(b["auth"]["username"], b["auth"]["password"])

    def bei_nachricht(_k, _u, m):
        if not getattr(m, "retain", False):
            ergebnis["q"] = m.payload.decode("utf-8", "replace")
            fertig.set()
    k.on_message = bei_nachricht
    k.connect(b["host"], b["port"], 30)
    k.loop_start()
    try:
        k.subscribe(thema + "/quittung", qos=0)
        time.sleep(0.3)
        k.publish(thema, text, qos=0, retain=False)
        fertig.wait(warten)
    finally:
        k.loop_stop()
        k.disconnect()
    return ergebnis["q"]


def signal_test() -> tuple:
    """(ergebnis, text): aus | nicht_moeglich | angenommen | abgelehnt | keine_antwort. Ab Werk wirkungslos:
    ist 'Meldungen an SignalBot' aus, wird nichts gesendet."""
    cfg = config()
    if not cfg.get("signal_ein"):
        return ("aus", "Meldungen an SignalBot sind aus - es wurde nichts gesendet.")
    thema = signal_test_thema(cfg.get("signal_praefix"))
    if not thema:
        return ("nicht_moeglich", "Das Praefix des SignalBot-Plugins ist ungueltig.")
    try:
        if _SIGNAL_TEST_SITZUNG is not None:
            q = _SIGNAL_TEST_SITZUNG(thema, SIGNAL_TEST_TEXT, SIGNAL_TEST_WARTEN)
        else:
            q = _signal_test_sitzung(thema, SIGNAL_TEST_TEXT, SIGNAL_TEST_WARTEN)
    except ImportError:
        return ("nicht_moeglich", "paho-mqtt fehlt in der virtuellen Umgebung.")
    except Exception as err:  # noqa: BLE001
        return ("nicht_moeglich", "Broker nicht erreichbar (%s)." % type(err).__name__)
    e = signal_test_auswerten(q)
    return (e, mqtt_wert_saeubern(str(q or ""), 120).replace(";", " ").replace("\n", " "))


# ---- Der Wartetakt (alle 10 s) ----
def auftrag_ausfuehren(liste: list, cfg: dict, b: dict) -> tuple:
    """Ein Auftrag des Dienstes selbst (Vorklima, Ankunft) - derselbe Weg wie die Warteschlange:
    befehl_ausfuehren() mit Zeitgrenze, Bremse und Buchung erst nach Erfolg."""
    try:
        ok, meldung, _ = befehl_ausfuehren(liste, cfg, b)
    except Exception as err:  # noqa: BLE001
        ok, meldung = 0, fehlertext(err)
    return (ok, meldung)


def g41_takt(liste: list, cfg: dict, merker: dict, stand: dict) -> bool:
    """Vorklimatisierung, Ankunft, Preisprobe, Vorklima-Thema. True = Sofortabruf (Wartetakt verlassen)."""
    jetzt = time.time()
    vorher = json.dumps(merker, sort_keys=True)
    sofort = False
    auftrag = vorklima_takt(_HORCHER, cfg, merker, stand, jetzt)
    if auftrag is not None:
        e_ok, e_meldung = auftrag_ausfuehren(liste, cfg, auftrag)
        _LOG.info("Vorklimatisierung: ok=%s %s", e_ok, e_meldung)
        vorklima_ergebnis(merker, auftrag, e_ok)
        sofort = auftrag.get("aktion") == "abruf" and e_ok == 1
    if not sofort and ankunft_takt(_HORCHER, cfg, merker, stand, jetzt):
        e_ok, e_meldung = auftrag_ausfuehren(liste, cfg, {"aktion": "abruf", "von": "ankunft"})
        _LOG.info("Ankunft: ok=%s %s", e_ok, e_meldung)
        sofort = e_ok == 1
    ladung_probe(_HORCHER, cfg, jetzt)
    vorklima_melden(cfg, merker, jetzt)
    # Nachtrag 4: Restminuten je Minute nachfuehren (nur Geaendertes).
    restminuten_nachfuehren(cfg, jetzt)
    if json.dumps(merker, sort_keys=True) != vorher:
        json_schreiben(DATEI_VORKLIMA, merker, 0o600)
    return sofort


def ladeempfehlung(cfg: dict) -> int | None:
    """1 = laden empfohlen, 0 = nicht, None = keine Aussage.

    None nur, wenn KEIN Thema oder keine Grenze eingetragen ist. Seit dem
    Durchgang 02.10.2026 (M4; Regeln/07 "Eine Empfehlung, die verstummt,
    sendet 0, nicht nichts"): ist ein Thema eingetragen und kommt keine Zahl
    ("-", leer, nichts) oder ist der letzte Wert aelter als das Dreifache des
    Abruftakts, ist die Empfehlung 0. Bis 0.9.26 blieb sie dann auf dem
    letzten Wert stehen - retained, also auch nach jedem Neustart "frisch 1"
    (gemessen, MQTT-Bericht V4).
    """
    if cfg.get("ladeempf_quelle") == "haus":
        # Gruppe 41 (E8b): Endpreis jetzt aus haus/strompreis/aktuell - nur bei frischer Quelle (Zaehler live
        # binnen 120 s). Schweigt sie oder kommt '-', geht wie beim eigenen Thema eine 0 hinaus (M4).
        grenze = komma(cfg.get("empf_grenze"))
        if grenze is None:
            return None
        if not haus_frisch(_HORCHER.haus, "strompreis", time.time()):
            melde_gebremst("empf_haus_still", "Ladeempfehlung: haus/strompreis schweigt (kein Zaehlerwechsel "
                           "binnen 120 s). Sie geht als 0 hinaus.", 3600)
            return 0
        v = komma(haus_wert(_HORCHER.haus, "strompreis", "haus/strompreis/aktuell"))
        if v is None:
            return 0
        return 1 if ((v <= grenze) if cfg.get("empf_kleiner") else (v >= grenze)) else 0
    thema = str(cfg.get("empf_thema") or "")
    grenze = komma(cfg.get("empf_grenze"))
    if thema == "" or grenze is None:
        return None
    v = _HORCHER.zahl(thema)
    alter = time.time() - _HORCHER.zeiten.get(thema, 0.0)
    if v is None or alter > 3 * max(TAKT_MIN, ganz(cfg.get("intervall"), 300)):
        return 0
    return 1 if ((v <= grenze) if cfg.get("empf_kleiner") else (v >= grenze)) else 0


# Gruppe 41 (0.9.33): abfahrt_pruefen() und der Merker _ABFAHRT_GEMELDET sind fort. Befunde: B1 - der
# Merker stand VOR dem Ergebnis (ein Fehlschlag sendete nie wieder); B2 - er lag nur im Speicher (nach
# einem Neustart dieselbe Abfahrt noch einmal); B4 - das Thema hatte keine Altersgrenze. Ersetzt durch
# vorklima_takt() im Wartetakt (g41_takt()), Merker in vorklima.json, Fahrzeug einstellbar (B5).


def zustand_schreiben(**felder) -> None:
    z = json_lesen(DATEI_ZUSTAND)
    z.update(felder)
    z["ts"] = int(time.time())
    json_schreiben(DATEI_ZUSTAND, z)


# ---------------------------------------------------------------------------
# Die Bibliothek vorbereiten
# ---------------------------------------------------------------------------
def ntp_entschaerfen() -> None:
    """CarConnectivity fragt beim Anlegen einen Zeitserver.

    In der Bibliothek faengt ntp_time_delta() nur NTPException ab. Kann der
    LoxBerry pool.ntp.org nicht aufloesen - kein DNS nach aussen, gesperrter
    UDP-Port 123 -, kommt eine socket.gaierror durch und der Konstruktor
    stirbt, bevor irgendetwas passiert ist. Die Abfrage dient nur einer
    Warnung ueber eine abweichende Systemzeit; sie darf den Dienst nicht
    verhindern.

    Deshalb wird sie hier gekapselt. Geprueft an carconnectivity 0.11.10.
    """
    try:
        import carconnectivity.util as util
        import carconnectivity.carconnectivity as kern
    except ImportError:
        return
    original = getattr(util, "ntp_time_delta", None)
    if original is None or getattr(original, "_lb_gekapselt", False):
        return

    def sicher(server: str = "pool.ntp.org"):
        try:
            return original(server)
        except OSError as err:
            melde_gebremst("ntp", f"Zeitserver nicht erreichbar ({err}) - die Pruefung der "
                                  f"Systemzeit entfaellt. Das ist kein Fehler des Plugins.",
                           86400)
            return None

    sicher._lb_gekapselt = True
    util.ntp_time_delta = sicher
    if hasattr(kern, "ntp_time_delta"):
        kern.ntp_time_delta = sicher


def bibliothek_config(z: dict, cfg: dict) -> dict:
    """Die Konfiguration, die CarConnectivity erwartet.

    Zugangsdaten stehen nur hier im Arbeitsspeicher, nie in einer Datei, die
    die Oberflaeche anzeigt.
    """
    connector = {
        "username": z["email"],
        "password": z["passwort"],
        "interval": max(TAKT_MIN, int(cfg["intervall"])),
    }
    if z["spin"]:
        connector["spin"] = z["spin"]
    if cfg.get("zugriff_erzwingen"):
        # Manche Fahrzeuge melden die Faehigkeit ACCESS nicht, koennen den
        # Tuerzustand aber sehr wohl liefern. Der Connector bietet dafuer
        # diesen Schalter an.
        connector["force_enable_access"] = True
    return {
        "carConnectivity": {
            "log_level": "WARNING",
            "connectors": [{"type": "volkswagen", "config": connector}],
        }
    }


# ---------------------------------------------------------------------------
# Dienst
# ---------------------------------------------------------------------------
def signal_behandeln(*_):
    global _LAUF
    _LAUF = False
    _LOG.info("Beendigungssignal erhalten - Dienst haelt an.")


# ---------------------------------------------------------------------------
# Zeitgrenze fuer einen Abruf
#
# cc.fetch_all() geht ueber die Bibliothek carconnectivity und von dort ueber
# requests ins Netz. Nachgemessen gegen ein Gegenstueck, das die Verbindung
# annimmt und danach schweigt:
#
#   requests.get() ohne timeout            haengt unbegrenzt (nach 8 s von
#                                          aussen abgebrochen)
#   dasselbe mit socket.setdefaulttimeout  haengt EBENFALLS unbegrenzt
#   dasselbe mit signal.alarm(2)           2,0 s, sauberer ReadTimeout
#
# Der naheliegende Weg ueber setdefaulttimeout wirkt also nicht: urllib3 gibt
# beim Verbindungsaufbau eine eigene Zeitgrenze an und ueberschreibt die
# Vorgabe des Sockets damit. Was wirkt, ist der Wecker.
#
# Dass daraus ein ReadTimeout wird und keine nackte Ausnahme, ist der
# angenehme Teil: requests deutet den unterbrochenen Lesevorgang selbst und
# raeumt seine Verbindung ab. Die Fehlerbehandlung der Bibliothek greift also
# wie bei jeder anderen Stoerung.
#
# signal.alarm geht nur im Hauptstrang - dienst() laeuft dort (main() ruft
# sie unmittelbar auf, es gibt keine Threads). SIGALRM ist sonst unbenutzt;
# belegt sind nur SIGTERM und SIGINT.
GRENZE_ABRUF = 180

# Dieselbe Ueberlegung fuer den SCHREIBWEG. attr.value = ... ist keine
# Zuweisung, sondern eine blockierende HTTP-Anfrage: der Connector setzt fuer
# seine Sitzung timeout 180 mit drei Wiederholungen, also bis zu rund 540
# Sekunden je Befehl. In dieser Zeit wuerde weder die Warteschlange abgefragt
# noch der Takt weitergezaehlt. 120 s sind reichlich fuer eine Anfrage, die
# normalerweise unter einer Sekunde beantwortet wird.
GRENZE_BEFEHL = 120


class Zeitgrenze:
    """Bricht einen haengenden Aufruf nach $sekunden ab - wo das geht.

    Drei Voraussetzungen, und alle drei werden geprueft statt angenommen:

      1. Eine Zeit groesser null.
      2. Der Hauptstrang. signal.alarm wirkt nur dort; der Netzstrang des
         MQTT-Horchers laeuft nie hier hindurch.
      3. SIGALRM muss es geben. Auf dem LoxBerry (Linux) gibt es das Signal,
         auf dem Windows-Arbeitsplatz nicht - dort stirbt
         signal.signal(signal.SIGALRM, ...) mit AttributeError. Bis 0.9.11
         fiel das nicht auf, weil die Zeitgrenze nur in dienst() stand und
         dienst() nur auf dem Geraet laeuft. Seit 0.9.12 liegt sie in
         befehl_ausfuehren() und damit im Weg jedes Aufrufers, auch der
         Hauswerkzeuge. Ein Wecker, den es auf dieser Plattform nicht gibt,
         entfaellt - er reisst nicht den Aufrufer mit.

    Wo der Wecker entfaellt, ist der Aufruf UNGESCHUETZT. Das ist auf dem
    Arbeitsplatz richtig (dort haengt keine echte Gegenstelle) und auf dem
    Geraet gaebe es den Fall nicht.
    """

    def __init__(self, sekunden: int, was: str = "Abruf"):
        self.sekunden = int(sekunden)
        self.was = was
        self.alt = None

    def moeglich(self) -> bool:
        return (self.sekunden > 0
                and hasattr(signal, "SIGALRM")
                and threading.current_thread() is threading.main_thread())

    def __enter__(self):
        if self.moeglich():
            self.alt = signal.signal(signal.SIGALRM, self._schlagen)
            signal.alarm(self.sekunden)
        return self

    def _schlagen(self, *_):
        raise TimeoutError(
            "{0} hat laenger als {1} s gebraucht - abgebrochen.".format(self.was, self.sekunden))

    def __exit__(self, *_):
        if self.alt is not None:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, self.alt)
            self.alt = None
        return False


# ---------------------------------------------------------------------------
# Sprachausgabe (Nr. 36 b, Stufe 2, seit 0.9.29)
#
# Der Dienst sagt einzelne Ereignisse an - ueber die gemeinsame Sprachausgabe
# der Plugins dieses Hauses (webfrontend/html/sprachausgabe.php). Die gibt es
# nur in PHP; gesprochen wird deshalb ueber die Bruecke bin/vw_ansage.php. Der
# Auftrag geht ueber die STANDARDEINGABE, nie ueber die Kommandozeile (die
# sieht jeder in der Prozessliste), und er traegt keinen Text: den Satz baut
# die Bruecke aus der Sprachdatei. Ob und wohin gesprochen wird, entscheidet
# die Bruecke aus dem Block tts der Konfiguration (ab Werk aus). Der Dienst
# sieht dort nur nach, ob die Ausgabe aus ist - dann ruft er gar nicht erst.
#
# Angesagt wird nur ein WECHSEL, nie ein Wert im Takt:
#   laden_fertig   laedt 1 -> 0, Ladestand nicht nachweislich unter der Ladegrenze
#   laden_abbruch  laedt 1 -> 0 und Ladestand mehr als 1 Punkt unter der Ladegrenze,
#                  oder Ladezustand "error"
#   offen          nicht in Fahrt und unverriegelt, Tuer oder Fenster offen - im
#                  zweiten Abruf in Folge (wie die Einschaltverzoegerung der
#                  Baustein-Liste, Baustein 15)
#   licht          nicht in Fahrt und Licht an - im zweiten Abruf in Folge
#   klima          Klimatisierung 1 -> 0, nicht waehrend der Fahrt
#   ausfall        der dritte gescheiterte Abruf in Folge (die Grenze von OK=0),
#                  erst nachdem seit dem Start wenigstens einer gelungen ist
# Die erste Beobachtung eines Fahrzeugs nach dem Start setzt nur den
# Ausgangsstand: ein Neustart spricht nie. Jeder Anlass hat einen eigenen Haken
# (ansage_<anlass>) und eine Wiederholsperre je Anlass und Fahrzeug
# (ANSAGE_SPERRE_S), die einen Neustart des Dienstes ueberlebt. MQTT, Loxone
# und das Abbild laufen davor und unabhaengig davon.
# ---------------------------------------------------------------------------
SKRIPT_ANSAGE = SELF / "vw_ansage.php"
DATEI_ANSAGE_SPERRE = PDATA / "ansage_sperre.json"
ANSAGE_ANLAESSE = ("laden_fertig", "laden_abbruch", "offen", "licht", "klima", "ausfall")
ANSAGE_SPERRE_S = 3600     # derselbe Anlass fuer dasselbe Fahrzeug hoechstens einmal je Stunde
ANSAGE_FRIST_S = 25        # Bruecke: PHP-Start plus Sprechfrist 20 s (vw_ansage_k(); Modul 1.2.1: in jedem Weg, B1)
ANSAGE_FOLGE = 2           # offen, licht: so viele Abrufe in Folge
ANSAGE_AUSFALL_FOLGE = 3   # dieselbe Grenze wie OK=0 (3x Takt)
_ANSAGE_STAND: dict = {}   # je Fahrzeugnummer der zuletzt gesehene Stand - nur im Speicher
_ANSAGE_DIENST = {"ok_gesehen": False, "fehler_folge": 0}


def _ansage_in_fahrt(f: dict) -> bool:
    return f.get("zustand") in (2, 3)


def ansage_ereignisse(nr: str, f: dict) -> list:
    """Die Anlaesse eines Fahrzeugs aus dem Vergleich mit dem zuletzt gesehenen
    Stand. Nur ein frisches Abbild (ok=1) kommt hierher. Rueckgabe: Liste von
    (anlass, daten) - daten ohne Text, nur Name, Ladestand und Ladegrenze."""
    alt = _ANSAGE_STAND.get(nr)
    vorher = alt or {}
    offen_jetzt = (not _ansage_in_fahrt(f)) and (
        f.get("verriegelt") == 0 or f.get("tueren_offen") == 1 or f.get("fenster_offen") == 1)
    licht_jetzt = (not _ansage_in_fahrt(f)) and f.get("licht_an") == 1
    neu = {
        # Ein unbekannter Zustand (None) laesst den bekannten stehen - wie fortschreiben().
        "laedt": f.get("laedt") if f.get("laedt") is not None else vorher.get("laedt"),
        "klima": f.get("klima_an") if f.get("klima_an") is not None else vorher.get("klima"),
        # Die erste Beobachtung zaehlt als schon gemeldet: ein Fahrzeug, das beim Start
        # offen steht, wird nicht angesagt.
        "offen": (vorher.get("offen", 0) + 1 if alt is not None else ANSAGE_FOLGE) if offen_jetzt else 0,
        "licht": (vorher.get("licht", 0) + 1 if alt is not None else ANSAGE_FOLGE) if licht_jetzt else 0,
    }
    _ANSAGE_STAND[nr] = neu
    if alt is None:
        return []
    name = str(f.get("name") or f.get("modell") or "")[:60]
    aus = []
    if vorher.get("laedt") == 1 and neu["laedt"] == 0:
        soc, grenze = f.get("soc"), f.get("ladegrenze")
        try:
            unter = soc is not None and grenze is not None and float(soc) < float(grenze) - 1
        except (TypeError, ValueError):
            unter = False
        stoerung = str(f.get("ladezustand_text") or "").lower() == "error"
        if unter or stoerung:
            aus.append(("laden_abbruch", {"name": name,
                                          "soc": soc if unter else None,
                                          "grenze": grenze if unter else None}))
        else:
            aus.append(("laden_fertig", {"name": name, "soc": soc}))
    if neu["offen"] == ANSAGE_FOLGE:
        aus.append(("offen", {"name": name}))
    if neu["licht"] == ANSAGE_FOLGE:
        aus.append(("licht", {"name": name}))
    if vorher.get("klima") == 1 and neu["klima"] == 0 and not _ansage_in_fahrt(f):
        aus.append(("klima", {"name": name}))
    return aus


def ansage_ausfall(ok: int, fehler_folge: int) -> bool:
    """Ist das der Abruf, mit dem die Daten als ausgefallen gelten?"""
    d = _ANSAGE_DIENST
    vorher = d["fehler_folge"]
    d["fehler_folge"] = fehler_folge
    if ok:
        d["ok_gesehen"] = True
        return False
    return bool(d["ok_gesehen"]) and vorher < ANSAGE_AUSFALL_FOLGE <= fehler_folge


def _ansage_aus() -> bool:
    """Ist die Ausgabe aus? Nur der Modus wird angesehen; geprueft wird der Block
    in der Bruecke. Fehlt er oder ist er unlesbar, gilt er als aus (Vorgabe)."""
    roh, _lage = _config_roh()
    t = roh.get("tts") if isinstance(roh, dict) else None
    return not isinstance(t, dict) or t.get("mode", "aus") == "aus"


def _ansage_sperre(schluessel: str, jetzt: int) -> bool:
    """Gesperrt? Sonst wird die Sperre gesetzt (und abgelaufene Eintraege fallen weg)."""
    s = json_lesen(DATEI_ANSAGE_SPERRE)
    t = s.get(schluessel)
    if isinstance(t, (int, float)) and not isinstance(t, bool) and 0 <= jetzt - t < ANSAGE_SPERRE_S:
        return True
    s = {k: v for k, v in s.items()
         if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= jetzt - v < ANSAGE_SPERRE_S}
    s[schluessel] = jetzt
    json_schreiben(DATEI_ANSAGE_SPERRE, s)
    return False


def ansage_senden(anlass: str, nr: str, daten: dict, cfg: dict) -> None:
    """Einen Anlass ueber die Bruecke ansagen. Ins Protokoll kommen Anlass,
    Fahrzeugnummer und das Ergebnis (Stand, Art, HTTP, Zeichenzahl, Kennung) -
    nie der Text, nie ein Token."""
    if anlass not in ANSAGE_ANLAESSE or not cfg.get("ansage_" + anlass):
        return
    if _ansage_aus():
        return
    jetzt = int(time.time())
    if _ansage_sperre(anlass + ":" + str(nr), jetzt):
        _LOG.info("Ansage %s (Fahrzeug %s) unterdrueckt - hoechstens eine je %d s.",
                  anlass, nr, ANSAGE_SPERRE_S)
        return
    auftrag = {"anlass": anlass, "nr": ganz(nr, 0), "name": str(daten.get("name") or "")}
    for k in ("soc", "grenze"):
        v = daten.get(k)
        auftrag[k] = v if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    try:
        e = subprocess.run(["php", str(SKRIPT_ANSAGE), PNAME],
                           input=json.dumps(auftrag, ensure_ascii=False).encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=ANSAGE_FRIST_S, check=False)
    except (OSError, subprocess.SubprocessError) as err:
        melde_gebremst("ansage_ruf", "Ansage %s (Fahrzeug %s): die Bruecke vw_ansage.php war "
                                     "nicht aufrufbar (%s)." % (anlass, nr, type(err).__name__), 3600)
        return
    zeilen = e.stdout.decode("ascii", "replace").strip().splitlines()
    zeile = zeilen[-1] if zeilen else ""
    f = {}
    if zeile.startswith("ANSAGE;"):
        for teil in zeile.split(";")[1:]:
            if "=" in teil:
                k, v = teil.split("=", 1)
                f[k] = re.sub(r"[^A-Za-z0-9_.:,|/\-]", "?", v)[:120]
    _LOG.log(logging.INFO if e.returncode in (0, 3) else logging.WARNING,
             "Ansage %s (Fahrzeug %s): rc=%d stand=%s art=%s http=%s zeichen=%s kennung=%s",
             anlass, nr, e.returncode, f.get("STAND", "?"), f.get("ART", "?"),
             f.get("HTTP", "?"), f.get("ZEICHEN", "?"), f.get("KENNUNG", "?"))


def ansagen(cfg: dict, fahrzeuge: dict, ok: int, fehler_folge: int) -> None:
    """Nach jedem Abruf: Anlaesse erkennen und ansagen."""
    if ansage_ausfall(ok, fehler_folge):
        ansage_senden("ausfall", "0", {}, cfg)
        signal_senden(cfg, "ausfall", "0", SIGNAL_TEXTE["ausfall"])
    for nr in sorted(fahrzeuge, key=lambda n: ganz(n, 0)):
        f = fahrzeuge[nr]
        # Ein ausgefallenes Fahrzeug traegt die Werte des letzten Erfolgs - daraus
        # entsteht kein Wechsel.
        if not isinstance(f, dict) or not f.get("ok"):
            continue
        for anlass, daten in ansage_ereignisse(nr, f):
            ansage_senden(anlass, nr, daten, cfg)
            # Gruppe 41 (E9): dieselben Ereignisse an SignalBot - eine Erkennung fuer beide Wege.
            if anlass in SIGNAL_TEXTE:
                wer = str(daten.get("name") or "") or ("Fahrzeug %s" % nr)
                signal_senden(cfg, anlass, nr, SIGNAL_TEXTE[anlass] % wer)


def stand_vorbelegen() -> dict:
    """Der letzte gute Stand aus loxone.json (C4, Durchgang 02.10.2026).

    Bis 0.9.26 begann jeder Dienst mit einem LEEREN Stand. Startete er
    waehrend einer Stoerung neu (Waechter, Update, Neustart des LoxBerry),
    schrieb der erste fehlgeschlagene Abruf "0 Fahrzeuge" ins Abbild, und
    der Endpunkt antwortete FAHRZEUG_UNBEKANNT statt mit den letzten Werten
    (gemessen, Codebericht Nr. 4, MQTT-Bericht V7). Uebernommen werden die
    Fahrzeuge samt Zeitstempel - ALTER bleibt damit ehrlich, und OK ist 0,
    bis ein Abruf gelingt."""
    lox = json_lesen(DATEI_LOXONE)
    f = lox.get("fahrzeuge")
    if not isinstance(f, dict) or not f:
        return {"ts": 0, "fahrzeuge": {}}
    return {"ts": ganz(lox.get("ts"), 0),
            "fahrzeuge": {str(k): v for k, v in f.items() if isinstance(v, dict)}}


def frueh_abbrechen(cfg: dict, grund: str, fehler: str) -> None:
    """Der Dienst endet, bevor er abrufen kann (Zugangsdaten fehlen, die
    Bibliothek weist schon beim Einrichten ab). Seit dem Durchgang
    02.10.2026 (M7) geht auch dann ok=0 hinaus und das Abbild nennt den
    Grund; bis 0.9.26 ging kein einziges Datagramm hinaus, und in Loxone
    blieb die letzte 1 stehen (MQTT-Bericht V8)."""
    z0 = json_lesen(DATEI_ZUSTAND)
    zaehler = (ganz(z0.get("zaehler"), -1) + 1) % 1000
    folge = ganz(z0.get("fehler_folge"), 0) + 1
    try:
        abbild_schreiben(stand_vorbelegen(), cfg, 0, fehler, grund, zaehler, folge)
    except Exception as err:  # noqa: BLE001 - die Meldung darf den Abbruch nicht verdecken
        _LOG.warning("Das Abbild liess sich nicht schreiben: %s", fehlertext(err))
    zustand_schreiben(ok=0, grund=grund, fehler=fehler, zaehler=zaehler,
                      fehler_folge=folge)


def dienst(einmal: bool = False) -> int:
    ntp_entschaerfen()
    from carconnectivity.carconnectivity import CarConnectivity

    cfg = config()
    z = zugang()
    if not z["email"] or not z["passwort"]:
        # Den Sollmerker MITNEHMEN. Bis 0.9.9 blieb er liegen, der Waechter
        # startete den Dienst jede Minute neu, jeder Versuch schrieb eine Zeile
        # ins Protokoll - rund 1440 am Tag, die die Logdatei umwaelzen. Ein
        # Sollmerker, der einen gescheiterten Start ueberlebt, macht daraus
        # eine Endlosschleife.
        try:
            (PDATA / "soll_laufen").unlink()
        except OSError:
            pass
        _LOG.error("Zugangsdaten fehlen. Reiter Einstellungen der Plugin-Oberflaeche oeffnen. "
                   "Der Dienst bleibt angehalten, bis sie eingetragen sind.")
        frueh_abbrechen(cfg, "ZUGANG_FEHLT", "Zugangsdaten fehlen.")
        return 1

    _LOG.info("Dienst startet (Takt %s s, Steuerung %s).",
              cfg["intervall"], "ein" if cfg.get("steuerung_ein") else "aus")

    try:
        # tokenstore_file: die Bibliothek legt hier ihre Anmeldemarken ab und
        # spart sich damit bei jedem Start eine neue Anmeldung. Die Datei
        # bekommt deshalb die Rechte 0600.
        cc = CarConnectivity(config=bibliothek_config(z, cfg),
                             tokenstore_file=str(DATEI_TOKEN),
                             cache_file=str(DATEI_ZWISCHEN))
    except Exception as err:  # noqa: BLE001
        meldung = fehlertext(err)
        _LOG.error("Die Bibliothek liess sich nicht einrichten: %s", meldung)
        frueh_abbrechen(cfg, grund_von(err), meldung)
        return 1
    rechte_sichern()

    stand: dict = stand_vorbelegen()
    # Gruppe 41 (B2): Merker der Vorklimatisierung und der Ankunft aus der Datei; Ladekosten der letzten
    # Ladung je Fahrzeug (Thema bis zur naechsten Ladung); offene Mitschriften (Zusatz: uebersteht Neustarts).
    g41 = vorklima_merker_lesen()
    lk = json_lesen(DATEI_LADEKOSTEN)
    _G41["ladung"] = {str(k): v for k, v in lk.items() if isinstance(v, dict)}
    ladung_mitschrift_holen(time.time())
    zyklus = 0
    zaehler = -1
    fehler_folge = 0
    stammdaten: dict[str, dict] = {}
    # Die zuletzt bekannte Fahrzeugliste. Bis 0.9.9 stand sie in der Schleife
    # und wurde bei jedem Durchgang auf [] gesetzt: nach EINEM fehlgeschlagenen
    # Abruf meldete jeder Schaltbefehl "Es ist noch kein Fahrzeug bekannt" -
    # mit der Bremse bis zu 3600 Sekunden lang, obwohl das Auto fuenf Minuten
    # vorher noch da war. Die Garage haelt die Objekte unveraendert weiter;
    # geleert wird sie erst in connector.shutdown().
    liste: list = []

    try:
        while _LAUF:
            cfg = config()  # Aenderungen aus der Oberflaeche ohne Neustart uebernehmen
            _HORCHER.pflegen(cfg)
            ok = 0
            fehler = ""
            grund = "OK"
            fahrzeuge: dict[str, dict] = {}
            entfernt: list = []
            try:
                # Mit Wecker: ohne ihn haelt ein Server, der die Verbindung
                # annimmt und dann schweigt, den ganzen Dienst an - samt
                # Befehlswarteschlange, die im selben Ablauf abgearbeitet wird.
                with Zeitgrenze(GRENZE_ABRUF, "Der Abruf bei Volkswagen"):
                    cc.fetch_all()
                garage = cc.get_garage()
                neu = list(garage.list_vehicles()) if garage is not None else []
                neu.sort(key=lambda f: str(wert(getattr(f, "vin", None)) or ""))
                if neu:
                    liste = neu
                # Die Nummer ist eine Adresse und bleibt bei ihrem
                # Fahrzeug - siehe fahrzeugnummern(). Bis 0.9.11 war sie der
                # Rang in dieser Liste und wanderte, sobald sich die
                # Fahrzeugmenge aenderte.
                vins = [str(wert(getattr(x, "vin", None)) or "") for x in neu]
                zuordnung = fahrzeugnummern(vins)
                for i, f in enumerate(neu, start=1):
                    vin = vins[i - 1] or str(i)
                    nr = zuordnung.get(vins[i - 1]) or i
                    stammdaten.setdefault(vin, {})
                    abbild = fahrzeug_abbilden(f, cfg, zyklus, stammdaten[vin])
                    for k, v in abbild.items():
                        if k.startswith(("inspektion", "oelservice")) and v is not None:
                            stammdaten[vin][k] = v
                    vorher = (stand.get("fahrzeuge") or {}).get(str(nr))
                    if not abbild.get("ok") and isinstance(vorher, dict) and vorher:
                        # AUSGEFALLENES FAHRZEUG (C5, Durchgang 02.10.2026;
                        # Entscheidung 8): Werte, VIN und Zeitstempel des
                        # letzten Erfolgs bleiben stehen, nur ok, Ausfaelle
                        # und Ausfalltext werden erneuert. Bis 0.9.26 ersetzte
                        # ein Ausfall alle Werte durch Striche und meldete am
                        # Endpunkt OK=1 (gemessen, Codebericht Nr. 5).
                        alt = dict(vorher)
                        alt.update(ok=0, ausfaelle=abbild.get("ausfaelle") or {},
                                   ausfalltext=abbild.get("ausfalltext") or "")
                        fahrzeuge[str(nr)] = alt
                        continue
                    # Was zwei Momentaufnahmen braucht: Ladevorgaenge und
                    # Fahrverbrauch. Der Takt ist die einzige Stelle, die den
                    # Zustand fortschreibt.
                    try:
                        abbild.update(fortschreiben(nr, abbild, cfg))
                        abbild.update(ladebilanz(nr))
                    except Exception as err:  # noqa: BLE001
                        melde_gebremst("fortschreibung",
                                       f"Ladeprotokoll: {fehlertext(err)}", 3600)
                    abbild["ladeempfehlung"] = ladeempfehlung(cfg)
                    # Zeitpunkt des letzten gelungenen Abrufs JE FAHRZEUG (C5, M8):
                    # daraus rechnet der Endpunkt ALTER fuer dieses Fahrzeug.
                    if abbild.get("ok"):
                        abbild["ts"] = int(time.time())
                    fahrzeuge[str(nr)] = abbild
                ok = 1 if fahrzeuge and any(x.get("ok") for x in fahrzeuge.values()) else 0
                if not neu:
                    fehler = "Das Konto fuehrt kein Fahrzeug."
                    grund = "KEIN_FAHRZEUG"
                elif not ok:
                    grund = "ABSCHNITTE"
                fehler_folge = 0 if ok else fehler_folge + 1
            except Exception as err:  # noqa: BLE001
                fehler = fehlertext(err)
                grund = grund_von(err)
                fehler_folge += 1
                melde_gebremst("abruf", f"Abruf fehlgeschlagen: {fehler}", 900)

            if ok and fahrzeuge:
                # Nummern, die der letzte Stand kannte und die ein GELUNGENER
                # Abruf nicht mehr liefert: das Konto fuehrt das Fahrzeug nicht
                # mehr (M5, Durchgang 02.10.2026).
                entfernt = sorted((n for n in (stand.get("fahrzeuge") or {})
                                   if n not in fahrzeuge), key=lambda n: ganz(n, 0))
                stand = {"ts": int(time.time()), "fahrzeuge": fahrzeuge}
                vorklima_nach_abruf(g41, cfg, fahrzeuge)
            vorklima_melden(cfg, g41, time.time())
            # Der Zaehler laeuft bei JEDEM Durchgang eine Stelle weiter, auch
            # bei einer Stoerung: er beantwortet die Frage "arbeitet der Dienst
            # noch", nicht "war der Abruf erfolgreich". Dafuer gibt es ok.
            zaehler = (zaehler + 1) % 1000
            abbild_schreiben(stand, cfg, ok, fehler, grund, zaehler, fehler_folge,
                             entfernt=entfernt)
            zustand_schreiben(ok=ok, fehler=fehler, grund=grund, zyklus=zyklus,
                              zaehler=zaehler, fehler_folge=fehler_folge,
                              pid=os.getpid(), intervall=cfg["intervall"],
                              horcher=1 if _HORCHER.verbunden else 0,
                              horcher_grund=_HORCHER.grund,
                              anzahl_fahrzeuge=len(stand["fahrzeuge"]),
                              haus=haus_zustand(_HORCHER.haus, time.time()))
            rechte_sichern()
            # Nr. 36 b: Ansagen - nach Abbild, MQTT und Zustand, damit eine haengende
            # Ausgabe keinen Meldeweg aufhaelt.
            try:
                ansagen(cfg, fahrzeuge, ok, fehler_folge)
            except Exception as err:  # noqa: BLE001
                melde_gebremst("ansage", f"Ansage: {fehlertext(err)}", 3600)
            zyklus += 1
            if einmal:
                return 0 if ok else 1

            rest = cfg["intervall"]
            if fehler_folge >= 3:
                rest = min(3600, cfg["intervall"] * min(8, fehler_folge))
                melde_gebremst("bremse",
                               f"{fehler_folge} Fehlversuche - naechster Abruf erst in {rest} s.",
                               1800)
            while rest > 0 and _LAUF:
                try:
                    # Der Wecker liegt seit 0.9.12 in befehl_ausfuehren(),
                    # also um den EINZELNEN Befehl. Bis 0.9.11 stand er hier
                    # und umfasste einen ganzen Durchgang durch alle
                    # Befehlsdateien; nach dem ersten Zuschlagen war er
                    # verbraucht, und abfahrt_pruefen() lag ohnehin
                    # ausserhalb. Beides ist dort beschrieben.
                    if warteschlange(liste, cfg):
                        break  # Sofortabruf angefordert
                except Exception as err:  # noqa: BLE001
                    _LOG.error("Warteschlange: %s", fehlertext(err))
                # Gruppe 41: alle 10 s Vorklimatisierung, Ankunft, Preisprobe; alle 30 s die Frische der
                # Haus-Quellen fuer den Reiter Test.
                if rest % 10 == 0:
                    try:
                        if rest % 30 == 0:
                            zustand_schreiben(haus=haus_zustand(_HORCHER.haus, time.time()))
                        if g41_takt(liste, cfg, g41, stand):
                            break  # Sofortabruf (Vorklima Versuch 2 oder Ankunft)
                    except Exception as err:  # noqa: BLE001
                        _LOG.error("Gruppe 41: %s", fehlertext(err))
                time.sleep(1)
                rest -= 1
    finally:
        _HORCHER.schliessen()
        try:
            cc.shutdown()
        except Exception:  # noqa: BLE001
            pass
        rechte_sichern()
    _LOG.info("Dienst beendet.")
    return 0


def rechte_sichern() -> None:
    """Die Bibliothek legt Marken- und Zwischenspeicherdatei selbst an.

    In der Markendatei stehen Anmeldemarken - sie gehoert niemandem sonst
    lesbar. Die Bibliothek setzt die Rechte nicht, also wird es hier nach
    jedem Schreibvorgang nachgeholt.
    """
    for p in (DATEI_TOKEN, DATEI_ZWISCHEN):
        try:
            if p.exists():
                os.chmod(p, 0o600)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Selbsttest - beantwortet ohne Netz und ohne Loxone, ob die Einrichtung traegt
# ---------------------------------------------------------------------------
def selbsttest() -> int:
    zeilen = []
    fehler = 0
    # Die Konfiguration GANZ am Anfang lesen. Bis zur Messung dieser Fassung
    # stand sie in der Mitte, und die S-PIN-Zeile darueber griff bereits auf
    # sie zu: UnboundLocalError, der Selbsttest brach ohne eine einzige
    # ausgegebene Zeile ab - und gab dabei 1 zurueck, was von aussen wie
    # "Beanstandungen gefunden" aussieht statt wie "abgestuerzt".
    c = config()

    v = sys.version_info
    if v >= (3, 9):
        zeilen.append(f"[OK]   Python {v.major}.{v.minor}.{v.micro} "
                      f"(carconnectivity verlangt 3.9 oder neuer)")
    else:
        fehler += 1
        zeilen.append(f"[FEHL] Python {v.major}.{v.minor}.{v.micro} ist zu alt - "
                      f"carconnectivity verlangt 3.9 oder neuer")

    venv = SELF / "venv" / "bin" / "python3"
    zeilen.append(f"[{'OK]  ' if venv.exists() else 'FEHL]'} Virtuelle Umgebung: {venv}")
    if not venv.exists():
        fehler += 1

    import importlib.metadata as md
    for paket, modul in (("carconnectivity", "carconnectivity.carconnectivity"),
                         ("carconnectivity-connector-volkswagen",
                          "carconnectivity_connectors.volkswagen.connector")):
        try:
            __import__(modul)
            try:
                fassung = md.version(paket)
            except Exception:  # noqa: BLE001
                fassung = "unbekannt"
            zeilen.append(f"[OK]   Bibliothek {paket} geladen, Fassung {fassung}")
        except Exception as err:  # noqa: BLE001
            fehler += 1
            zeilen.append(f"[FEHL] Bibliothek {paket} laesst sich nicht laden: {err}")

    for name, pfad in (("Konfiguration", PCONFIG), ("Daten", PDATA), ("Log", PLOG)):
        schreibbar = os.access(pfad, os.W_OK) if pfad.exists() else False
        zeilen.append(f"[{'OK]  ' if schreibbar else 'FEHL]'} Ordner {name} beschreibbar: {pfad}")
        if not schreibbar:
            fehler += 1

    z = zugang()
    # Ein Pruefknopf darf die FORM eines Geheimnisses beurteilen, nie seinen Wert zeigen.
    if z["email"] and "@" in z["email"]:
        zeilen.append(f"[OK]   Volkswagen-Benutzername hinterlegt ({z['email'][:2]}...@..., "
                      f"{len(z['email'])} Zeichen)")
    elif z["email"]:
        fehler += 1
        zeilen.append("[FEHL] Der Benutzername sieht nicht wie eine E-Mail-Adresse aus")
    else:
        fehler += 1
        zeilen.append("[FEHL] Kein Benutzername hinterlegt")
    if z["passwort"]:
        zeilen.append(f"[OK]   Passwort hinterlegt ({len(z['passwort'])} Zeichen, "
                      f"Inhalt wird nicht angezeigt)")
    else:
        fehler += 1
        zeilen.append("[FEHL] Kein Passwort hinterlegt")
    if z["spin"]:
        if z["spin"].isdigit() and len(z["spin"]) == 4:
            zeilen.append("[OK]   S-PIN hinterlegt (vier Ziffern)")
        else:
            fehler += 1
            zeilen.append(f"[FEHL] Die S-PIN hat {len(z['spin'])} Zeichen - erwartet werden "
                          f"genau vier Ziffern")
    elif c.get("eingreifend_ein"):
        # Seit 0.9.10 bietet das Plugin Ver- und Entriegeln an - der Satz
        # "das dieses Plugin nicht anbietet" war damit falsch geworden. Ein
        # Hilfetext, der eine Funktion verschweigt, die es gibt, ist so
        # schaedlich wie einer, der eine verspricht, die es nicht gibt.
        fehler += 1
        zeilen.append("[FEHL] Keine S-PIN hinterlegt, aber eingreifende Befehle sind "
                      "zugelassen. Ver- und Entriegeln wird ohne S-PIN abgewiesen; "
                      "Hupe und Lichthupe gehen auch ohne.")
    else:
        zeilen.append("[INFO] Keine S-PIN hinterlegt. Sie wird nur fuer Ver- und Entriegeln "
                      "gebraucht, und das steht hinter dem zweiten Haken, der aus ist.")

    for name, p, soll in (("Zugangsdatei", DATEI_ZUGANG, True),
                          ("Markendatei der Bibliothek", DATEI_TOKEN, False)):
        try:
            rechte = p.stat().st_mode & 0o777
            passt = (rechte & 0o077) == 0
            zeilen.append(f"[{'OK]  ' if passt else 'FEHL]'} Rechte {name}: {oct(rechte)} "
                          f"(erwartet 0o600)")
            if not passt:
                fehler += 1
        except OSError:
            if soll:
                fehler += 1
                zeilen.append(f"[FEHL] {name} fehlt: {p}")
            else:
                zeilen.append(f"[INFO] {name} noch nicht angelegt (entsteht beim ersten Abruf)")

    zeilen.append(f"[INFO] Takt {c['intervall']} s (Untergrenze der Bibliothek: {TAKT_MIN} s), "
                  f"Wartung alle {c['takt_wartung']} Takte")
    zeilen.append(f"[INFO] Schreibende Befehle: "
                  f"{'zugelassen' if c.get('steuerung_ein') else 'gesperrt'}, "
                  f"Zieltemperatur erlaubt von {c['temp_min']} bis {c['temp_max']} Grad")

    # Die MQTT-Zeilen nur beurteilen, wenn MQTT ueberhaupt eingeschaltet ist.
    # Bis 0.9.9 stand hier ein rotes Kreuz samt dem Satz "Ohne das kommt am
    # Miniserver nichts an" - auch bei ausgeschaltetem MQTT, wo der Satz
    # schlicht falsch ist: ueber den Endpunkt kommt sehr wohl etwas an.
    m = mqtt_zustand()
    if not c.get("mqtt_ein"):
        zeilen.append("[INFO] MQTT ist in den Einstellungen ausgeschaltet - der Miniserver "
                      "wird ueber den Endpunkt bedient. Das Gateway geht dieses Plugin "
                      "dann nichts an.")
    elif not m["gefunden"]:
        zeilen.append("[FEHL] Im general.json des LoxBerry ist kein MQTT-Abschnitt zu finden")
        fehler += 1
    elif m["autostart"]:
        zeilen.append(f"[OK]   MQTT-Gateway auf Autostart, Broker {m['broker']}:{m['brokerport']}, "
                      f"UDP-Eingang {m['udpport']}, "
                      f"{'Werte werden behalten (retain)' if c.get('mqtt_retain') else 'ohne retain'}")
    else:
        zeilen.append("[FEHL] Das MQTT-Gateway ist nicht auf Autostart gestellt "
                      "(System -> MQTT Gateway). Ohne das kommt am Miniserver nichts an.")
        fehler += 1

    # Die Themenliste: sendet der Dienst genau das, was die Oberflaeche
    # verspricht? Gezaehlt wird hier nur die eigene Seite; den Abgleich gegen
    # vw_mqtt_themen() macht der Reiter Test, der beide Dateien lesen kann.
    zeilen.append(f"[INFO] Themen je Fahrzeug: {len(MQTT_FELDER)} Zahlen, "
                  f"{len(MQTT_TEXTFELDER)} Texte, dazu {len(MQTT_OBEN)} oberhalb")

    # Der Horcher - nur wenn er gebraucht wird.
    if c.get("empf_thema") or (c.get("abfahrt_ein") and c.get("abfahrt_thema")):
        moeglich, grund = _HORCHER.moeglich()
        if moeglich:
            zeilen.append("[OK]   paho-mqtt ist vorhanden - Ladeempfehlung und "
                          "Vorklimatisierung sind moeglich")
        else:
            fehler += 1
            zeilen.append(f"[FEHL] {grund}")
    else:
        zeilen.append("[INFO] Weder Ladeempfehlung noch Vorklimatisierung eingerichtet - "
                      "es wird auf kein fremdes Thema gehorcht")

    # Die Entfernungsrechnung gegen einen bekannten Wert halten. Dieselbe
    # Formel steht in PHP; laufen sie auseinander, zeigt die Oberflaeche eine
    # andere Entfernung als Loxone. Kassel (Hauptbahnhof) -> Berlin sind rund 300 km.
    _e = entfernung_m(51.3183, 9.4896, 52.5200, 13.4050)
    if _e is not None and 295000 <= _e <= 303000:
        zeilen.append(f"[OK]   Entfernungsrechnung geeicht (Kassel-Berlin: {_e} m)")
    else:
        fehler += 1
        zeilen.append(f"[FEHL] Die Entfernungsrechnung liefert {_e} m statt rund 300000 m")

    if NICHT_INSTALLIERT:
        zeilen.append(f"[INFO] Unter {LBHOME} gibt es kein config/plugins/{PNAME} - dieses "
                      f"Plugin ist dort NICHT eingerichtet; der Aufruf kommt offenbar aus "
                      f"einem entpackten Archiv oder einem Pruefordner. Die Pfade unten "
                      f"zeigen deshalb auf Ordner, die es nicht gibt; das ist kein Fehler, "
                      f"aber auch keine Pruefung der Anlage.")

    lox = json_lesen(DATEI_LOXONE)
    if lox:
        alter = int(time.time()) - ganz(lox.get("ts"), 0)
        zeilen.append(f"[INFO] Letzter erfolgreicher Abruf vor {alter} s, ok={lox.get('ok')}, "
                      f"{lox.get('anzahl_fahrzeuge')} Fahrzeug(e)")
        for nummer, f in (lox.get("fahrzeuge") or {}).items():
            aus = f.get("ausfaelle") or {}
            zeilen.append(f"[INFO] Fahrzeug {nummer}: {f.get('modell') or 'ohne Modellangabe'}, "
                          f"{len(aus)} ausgefallene Abschnitte"
                          + (": " + ", ".join(sorted(aus)) if aus else ""))
    else:
        zeilen.append("[INFO] Es hat noch kein Abruf stattgefunden")

    zeilen.append("")
    zeilen.append("Nicht geprueft, weil dafuer ein Volkswagen-Konto und ein Fahrzeug noetig sind:")
    zeilen.append("  - ob die Anmeldung an der Volkswagen-Schnittstelle gelingt")
    zeilen.append("  - ob dieses Fahrzeug die abgefragten Werte ueberhaupt liefert")
    zeilen.append("  - ob die schreibenden Befehle am Fahrzeug die erwartete Wirkung haben")
    print("\n".join(zeilen))
    return 1 if fehler else 0


# ---------------------------------------------------------------------------
# Einmaliges Abraeumen der zurueckbehaltenen Dienstaussagen (0.9.24)
#
# Bis 0.9.23 gingen 'ok', 'fehler_folge', 'fehlertext' und
# 'fahrzeugN/ausfalltext' mit dem Befehlswort 'retain' hinaus. Wer auf
# 0.9.24 aktualisiert, hat sie also im Broker stehen - und weil sie jetzt
# fluechtig gesendet werden, ueberschreibt sie niemand mehr. Sie wuerden
# fuer immer dort stehenbleiben; beim Ausfalltext ist das der Fund, der die
# Klasse E ausgeloest hat.
#
# NICHT ueber den UDP-Eingang des Gateways, sondern unmittelbar am Broker.
# Begruendung (Regeln/07, Nachtrag vom 19.09.2026, am Geraet gemessen): der
# Eingang verwirft in Stoessen 17-70 % der Datagramme. Beschattungswaechter
# 0.9.19 und BatterieBMS 0.9.22 haben genau hier ihren Merker auf ein
# einziges fwrite gestuetzt - der Altwert stand danach weiter im Broker, und
# das Plugin hielt die Sache fuer erledigt. Der Weg ueber paho ist derselbe,
# den mqtt_leeren() seit 0.9.13 fuer die Deinstallation geht.
#
# Drei Dinge machen es belastbar:
#   1. Es wird nur geloescht, was WIRKLICH zurueckbehalten im Broker liegt -
#      gefunden ueber ein Abonnement, nicht geraten.
#   2. Nach dem Loeschen wird NACHGELESEN. Der Merker faellt erst, wenn ein
#      zweites Abonnement nichts mehr findet.
#   3. Der Merker traegt eine Kennung samt Themenpraefix. Ein Merker einer
#      Vorfassung, eine leere Datei oder ein gewechseltes Praefix lassen das
#      Abraeumen erneut laufen.
# Der gueltige Wert geht unmittelbar danach hinaus: der Aufrufer ruft im
# selben Durchgang mqtt_senden().
#
# Rueckgabe: 0 erledigt oder nichts zu tun, 1 es blieb etwas stehen,
# 2 nicht moeglich (keine Bibliothek, kein Broker, Anmeldung abgewiesen).
# ---------------------------------------------------------------------------
def mqtt_altlast_abraeumen(praefix: str, warten: float = 3.0) -> int:
    praefix = str(praefix or "").strip("/")
    if not praefix or "#" in praefix or "+" in praefix:
        return 2
    soll = RETAIN_ABRAEUM_KENNUNG + "|" + praefix
    try:
        if DATEI_RETAIN_GERAEUMT.read_text(encoding="utf-8").strip() == soll:
            return 0
    except (OSError, ValueError):
        pass

    def gehoert_dazu(thema: str) -> bool:
        if not thema.startswith(praefix + "/"):
            return False
        rest = thema[len(praefix) + 1:].split("/")
        if len(rest) == 1:
            return rest[0] in MQTT_ALTLAST
        return (len(rest) == 2 and rest[0].startswith("fahrzeug")
                and rest[0][8:].isdigit() and 1 <= len(rest[0][8:]) <= 2
                and rest[1] in MQTT_ALTLAST)

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return 2
    z = mqtt_zustand()
    broker = z.get("broker") or "127.0.0.1"
    try:
        port = int(z.get("brokerport") or 1883)
    except (TypeError, ValueError):
        port = 1883

    gesehen: set = set()
    angemeldet = threading.Event()
    code = {"wert": None}

    def bei_verbindung(_k, _d, _f, *rest):
        try:
            code["wert"] = int(getattr(rest[0], "value", rest[0]) or 0) if rest else 0
        except (TypeError, ValueError):
            code["wert"] = 0
        angemeldet.set()

    def bei_nachricht(_k, _d, n):
        if n.retain and n.payload and gehoert_dazu(n.topic):
            gesehen.add(n.topic)

    try:
        k = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except (AttributeError, TypeError):
        try:
            k = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        except (AttributeError, TypeError):
            k = mqtt.Client()
    k.on_connect = bei_verbindung
    k.on_message = bei_nachricht
    if z.get("benutzer"):
        k.username_pw_set(z["benutzer"], z.get("passwort") or None)
    try:
        k.connect(broker, port, 30)
    except Exception as err:  # noqa: BLE001
        melde_gebremst("retain_altlast",
                       f"MQTT: Broker {broker}:{port} nicht erreichbar "
                       f"({fehlertext(err)}) - die zurueckbehaltenen Altwerte aus "
                       f"frueheren Fassungen stehen noch im Broker.")
        return 2
    k.loop_start()
    try:
        if not angemeldet.wait(10) or code["wert"]:
            melde_gebremst("retain_altlast",
                           "MQTT: der Broker hat die Anmeldung nicht angenommen - die "
                           "zurueckbehaltenen Altwerte aus frueheren Fassungen stehen "
                           "noch im Broker.")
            return 2
        k.subscribe(praefix + "/#")
        time.sleep(warten)
        k.unsubscribe(praefix + "/#")
        zu_leeren = sorted(gesehen)
        for thema in zu_leeren:
            info = k.publish(thema, b"", qos=1, retain=True)
            try:
                info.wait_for_publish(5)
            except TypeError:           # paho 1.x vor 1.6 kennt kein timeout
                info.wait_for_publish()
        # NACHLESEN: ein neues Abonnement bekommt alles, was noch behalten ist.
        gesehen.clear()
        k.subscribe(praefix + "/#")
        time.sleep(warten)
        rest = sorted(gesehen)
    except Exception as err:  # noqa: BLE001
        melde_gebremst("retain_altlast",
                       f"MQTT: das Abraeumen der Altwerte scheiterte ({fehlertext(err)}).")
        return 2
    finally:
        k.loop_stop()
        try:
            k.disconnect()
        except Exception:  # noqa: BLE001
            pass
    if rest:
        _LOG.warning("MQTT: %d von %d zurueckbehaltenen Altwerten stehen noch im Broker "
                     "(zum Beispiel %s) - es wird beim naechsten Durchgang erneut "
                     "versucht.", len(rest), len(zu_leeren), rest[0])
        return 1
    # ERST JETZT der Merker - nach dem Nachlesen, nicht nach dem Senden.
    try:
        PDATA.mkdir(parents=True, exist_ok=True)
        DATEI_RETAIN_GERAEUMT.write_text(soll + "\n", encoding="utf-8")
    except OSError as err:
        _LOG.warning("MQTT: der Merker %s liess sich nicht schreiben (%s) - das "
                     "Abraeumen laeuft beim naechsten Durchgang erneut.",
                     DATEI_RETAIN_GERAEUMT, err)
    if zu_leeren:
        _LOG.info("MQTT: %d zurueckbehaltene Altwerte aus frueheren Fassungen geloescht "
                  "und nachgelesen (%s).", len(zu_leeren), ", ".join(zu_leeren))
    return 0


# ---------------------------------------------------------------------------
# Behaltene Themen leeren - fuer die Deinstallation
#
# Entscheidung des Hausherrn vom 17.09.2026: wer das Plugin entfernt, soll
# keine behaltenen Werte im Broker zuruecklassen. Ein Miniserver, der nach
# dem Entfernen neu startet, bekaeme sonst den letzten Ladestand und die
# letzte Position als frische Werte ausgeliefert - und niemand schriebe sie
# je wieder fort.
#
# Geleert wird NUR, was dieser Dienst selbst sendet: das Praefix aus der
# Konfiguration und darunter genau die Namen aus MQTT_OBEN, MQTT_FELDER und
# MQTT_TEXTFELDER. Ein fremdes Thema unter demselben Praefix - ein zweites
# Plugin, das zufaellig "volkswagen/..." benutzt - bleibt stehen.
#
# Gesendet wird ueber paho mit der Anmeldung aus general.json, nicht ueber
# den UDP-Eingang des Gateways: der verwirft Datagramme in Stoessen
# (Regeln/07), und eine Loeschung, die zur Haelfte ankommt, meldete
# trotzdem Erfolg. Hinterher wird NACHGEMESSEN, ob noch etwas behalten ist.
#
# Rueckgabe: 0 geleert oder nichts zu leeren, 1 Themen blieben stehen,
# 2 nicht moeglich (keine Bibliothek, kein Broker, Anmeldung abgewiesen).
# ---------------------------------------------------------------------------
def mqtt_eigenes_thema(praefix: str, thema: str) -> bool:
    """Ist das ein Thema, das dieser Dienst unter dem Praefix sendet?

    Nur die eigenen Namen: MQTT_OBEN oben, darunter fahrzeug<1-2 Ziffern>/ mit
    den Namen aus MQTT_FELDER und MQTT_TEXTFELDER. Ein fremdes Thema unter
    demselben Praefix bleibt stehen."""
    if not thema.startswith(praefix + "/"):
        return False
    rest = thema[len(praefix) + 1:].split("/")
    if len(rest) == 1:
        return rest[0] in MQTT_OBEN
    return (len(rest) == 2 and rest[0].startswith("fahrzeug")
            and rest[0][8:].isdigit() and 1 <= len(rest[0][8:]) <= 2
            and rest[1] in (set(MQTT_FELDER) | set(MQTT_TEXTFELDER)))


def _praefix_gueltig(praefix) -> bool:
    return (isinstance(praefix, str) and praefix != ""
            and re.fullmatch(MUSTER_TEXT["mqtt_topic"][0], praefix) is not None)


def _broker_leeren(praefix: str, warten: float = 3.0) -> dict:
    """Die eigenen zurueckbehaltenen Themen unter EINEM Praefix am Broker
    loeschen und NACHLESEN - derselbe Weg wie mqtt_leeren() seit 0.9.13.

    Rueckgabe: {"rc": 0 erledigt | 1 es blieb etwas stehen | 2 nicht moeglich,
    "geleert": [...], "rest": [...], "grund": Klartext bei rc 2}."""
    erg = {"rc": 2, "geleert": [], "rest": [], "grund": ""}
    if not _praefix_gueltig(praefix):
        erg["grund"] = f"das Praefix '{praefix}' ist kein gueltiges Themenpraefix"
        return erg
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        erg["grund"] = "paho-mqtt fehlt"
        return erg
    z = mqtt_zustand()
    broker = z.get("broker") or "127.0.0.1"
    try:
        port = int(z.get("brokerport") or 1883)
    except (TypeError, ValueError):
        port = 1883
    gesehen: set = set()
    angemeldet = threading.Event()
    code = {"wert": None}

    def bei_verbindung(_k, _d, _f, *rest):
        try:
            code["wert"] = int(getattr(rest[0], "value", rest[0]) or 0) if rest else 0
        except (TypeError, ValueError):
            code["wert"] = 0
        angemeldet.set()

    def bei_nachricht(_k, _d, n):
        if n.retain and n.payload and mqtt_eigenes_thema(praefix, n.topic):
            gesehen.add(n.topic)

    try:
        k = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    except (AttributeError, TypeError):
        try:
            k = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        except (AttributeError, TypeError):
            k = mqtt.Client()
    k.on_connect = bei_verbindung
    k.on_message = bei_nachricht
    if z.get("benutzer"):
        k.username_pw_set(z["benutzer"], z.get("passwort") or None)
    try:
        k.connect(broker, port, 30)
    except Exception as err:  # noqa: BLE001
        erg["grund"] = f"Broker {broker}:{port} nicht erreichbar ({fehlertext(err)})"
        return erg
    k.loop_start()
    try:
        if not angemeldet.wait(10):
            erg["grund"] = f"Broker {broker}:{port} hat nicht geantwortet"
            return erg
        if code["wert"]:
            erg["grund"] = _CONNACK.get(code["wert"], f"Anmeldung mit Code {code['wert']} abgewiesen.")
            return erg
        k.subscribe(praefix + "/#")
        time.sleep(warten)
        k.unsubscribe(praefix + "/#")
        erg["geleert"] = sorted(gesehen)
        for thema in erg["geleert"]:
            info = k.publish(thema, b"", qos=1, retain=True)
            try:
                info.wait_for_publish(5)
            except TypeError:           # paho 1.x vor 1.6 kennt kein timeout
                info.wait_for_publish()
        # NACHLESEN: ein neues Abonnement bekommt alles, was noch behalten ist.
        gesehen.clear()
        k.subscribe(praefix + "/#")
        time.sleep(warten)
        erg["rest"] = sorted(gesehen)
        erg["rc"] = 1 if erg["rest"] else 0
    except Exception as err:  # noqa: BLE001
        erg["grund"] = f"das Leeren scheiterte ({fehlertext(err)})"
        erg["rc"] = 2
    finally:
        k.loop_stop()
        try:
            k.disconnect()
        except Exception:  # noqa: BLE001
            pass
    return erg


def mqtt_praefixe_pflegen(cfg: dict, warten: float = 3.0) -> None:
    """Abraeumen bei Praefixwechsel, "MQTT aus" und "Werte behalten aus"
    (M6, Durchgang 02.10.2026; Entscheidung 26).

    Bis 0.9.26 blieben nach einem Praefixwechsel alle Zustaende unter dem
    alten Praefix fuer immer im Broker, auch nach der Deinstallation, die nur
    das aktuelle leerte (gemessen, MQTT-Bericht V6: 50 Themen). Jetzt merkt
    sich der Dienst das Praefix, unter dem er zuletzt retained gesendet hat.
    Wird es nicht mehr retained beschickt, kommt es auf die Liste der
    vorgemerkten; jeder Durchgang raeumt sie am Broker ab (nur eigene Namen,
    mit Nachlesen) und streicht ein Praefix erst, wenn nachweislich nichts
    mehr steht. --mqtt-leeren (Deinstallation) leert sie ebenfalls.
    Ein Praefix, das wieder retained beschickt wird, faellt von der Liste.
    """
    praefix = str(cfg.get("mqtt_topic") or "volkswagen").strip("/") or "volkswagen"
    soll = praefix if (cfg.get("mqtt_ein") and cfg.get("mqtt_retain")) else ""
    d = json_lesen(DATEI_MQTT_PRAEFIXE)
    vorgemerkt = [p for p in (d.get("vorgemerkt") or []) if _praefix_gueltig(p)]
    alt = str(d.get("zuletzt") or "") if "zuletzt" in d else soll
    if alt and alt != soll and alt not in vorgemerkt and _praefix_gueltig(alt):
        vorgemerkt.append(alt)
        _LOG.info("MQTT: unter '%s' wird nicht mehr retained gesendet - die dort "
                  "behaltenen Werte dieses Plugins werden abgeraeumt.", alt)
    rest = []
    for p in vorgemerkt:
        if p == soll:
            continue
        erg = _broker_leeren(p, warten)
        if erg["rc"] == 0:
            _LOG.info("MQTT: %d behaltene Themen unter '%s/' abgeraeumt und nachgelesen.",
                      len(erg["geleert"]), p)
        else:
            rest.append(p)
            melde_gebremst("praefix_" + p,
                           f"MQTT: das Abraeumen unter '{p}/' ist nicht durch "
                           f"({erg['grund'] or str(len(erg['rest'])) + ' Themen stehen noch'}) - "
                           f"es bleibt vorgemerkt.", 3600)
    if d.get("zuletzt") != soll or d.get("vorgemerkt") != rest:
        json_schreiben(DATEI_MQTT_PRAEFIXE, {"zuletzt": soll, "vorgemerkt": rest})


def _udp_leeren(praefix: str) -> int:
    """Rueckfall der Deinstallation, wenn am Broker nicht geleert werden kann
    (paho fehlt, Broker nicht erreichbar): je eigenem Thema ein leeres
    'retain' an den UDP-Eingang des Gateways (M11, Durchgang 02.10.2026;
    Bauform Skoda-Connect-NG 0.9.28). UDP bestaetigt nichts - die Ausgabe sagt
    es. Fahrzeugnummern aus Abbild und Nummernliste, sonst 1 bis 4 (ein
    Loeschbefehl fuer ein Thema, das es nie gab, ist folgenlos)."""
    z = mqtt_zustand()
    if not z["udpport"]:
        print("<INFO> MQTT: auch kein UDP-Eingang des Gateways in general.json - die "
              "behaltenen Themen bleiben stehen und sind im Broker von Hand zu loeschen.")
        return 2
    nummern: list = []
    quellen = [[str(n) for n in nummern_lesen().values()]]
    for d in (json_lesen(DATEI_LOXONE), json_lesen(DATEI_CACHE)):
        if isinstance(d.get("fahrzeuge"), dict):
            quellen.append(list(d["fahrzeuge"].keys()))
    for q in quellen:
        for n in q:
            if re.fullmatch(r"[0-9]{1,2}", str(n)) and str(n) not in nummern:
                nummern.append(str(n))
    if not nummern:
        nummern = ["1", "2", "3", "4"]
    themen = list(MQTT_OBEN)
    for n in sorted(nummern, key=int):
        themen += [f"fahrzeug{n}/{feld}" for feld in MQTT_FELDER + MQTT_TEXTFELDER]
    geschickt = 0
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError as err:
        print(f"<INFO> MQTT: kein Socket ({err}) - die behaltenen Themen bleiben stehen.")
        return 2
    try:
        for th in themen:
            try:
                s.sendto(f"retain {praefix}/{th} ".encode("utf-8"), ("127.0.0.1", z["udpport"]))
                geschickt += 1
            except OSError:
                pass
            time.sleep(0.005)
    finally:
        s.close()
    print(f"<INFO> MQTT: Rueckfall ueber den UDP-Eingang {z['udpport']} des Gateways: "
          f"{geschickt} Loeschbefehle unter '{praefix}/' geschickt (leere Nutzlast, 'retain').")
    print("<INFO> MQTT: UDP bestaetigt nichts. Stehen die Themen danach noch, sind sie im "
          "Broker von Hand zu loeschen.")
    return 2


def _mqtt_leeren_eines(praefix: str, warten: float) -> int:
    erg = _broker_leeren(praefix, warten)
    if erg["rc"] == 0:
        if erg["geleert"]:
            print(f"<OK> MQTT: {len(erg['geleert'])} behaltene Themen unter {praefix}/ geleert "
                  f"und nachgemessen.")
        else:
            print(f"<INFO> MQTT: unter {praefix}/ war nichts behalten - nichts zu leeren.")
        return 0
    if erg["rc"] == 1:
        print(f"<WARNING> MQTT: {len(erg['rest'])} von {len(erg['geleert'])} behaltenen Themen unter "
              f"{praefix}/ stehen noch im Broker, zum Beispiel {erg['rest'][0]}.")
        return 1
    print(f"<INFO> MQTT: am Broker nicht moeglich - {erg['grund']}.")
    return _udp_leeren(praefix)


# ---------------------------------------------------------------------------
# Behaltene Themen leeren - fuer die Deinstallation
#
# Entscheidung des Hausherrn vom 17.09.2026: wer das Plugin entfernt, soll
# keine behaltenen Werte im Broker zuruecklassen. Geleert wird NUR, was
# dieser Dienst selbst sendet (mqtt_eigenes_thema()), am Broker ueber paho
# mit Nachmessen. Seit dem Durchgang 02.10.2026 unter ALLEN Praefixen, die
# das Plugin beschickt hat (M6: das eingestellte, das zuletzt retained
# beschickte und die vorgemerkten), und mit einem Rueckfall ueber den
# UDP-Eingang, wenn am Broker nichts geht (M11).
#
# Rueckgabe: 0 geleert oder nichts zu leeren, 1 Themen blieben stehen,
# 2 nicht am Broker moeglich (dann der UDP-Rueckfall).
# ---------------------------------------------------------------------------
def mqtt_leeren(warten: float = 3.0) -> int:
    praefixe: list = []
    d = json_lesen(DATEI_MQTT_PRAEFIXE)
    for p in ([str(config().get("mqtt_topic") or "volkswagen").strip("/") or "volkswagen"]
              + list(d.get("vorgemerkt") or []) + [d.get("zuletzt")]):
        if p in praefixe or not p:
            continue
        if not _praefix_gueltig(p):
            print(f"<WARNING> MQTT: das Themenpraefix '{p}' ist kein gueltiges Praefix - "
                  f"darunter wird nichts geleert.")
            continue
        praefixe.append(p)
    rc = 0
    for p in praefixe:
        rc = max(rc, _mqtt_leeren_eines(p, warten))
    return rc


# EINZELINSTANZ-SPERRE (Durchgang 02.10.2026, Baubericht Frage 7). dienst.sh
# sperrt seit C3 den Start; ein an dienst.sh vorbei gestarteter zweiter
# Dienst lief bis dahin trotzdem mit (gemessen: "zwei Dienste direkt
# gestartet: 2 laufen"), arbeitete dieselbe Warteschlange ab und fragte die
# Cloud doppelt. Die Sperre gilt fuer den Dauerlauf UND --einmal - beide
# arbeiten die Warteschlange ab -, nicht fuer --selbsttest und --mqtt-leeren.
# Der Deskriptor ist nicht vererbbar (os.open, PEP 446); der Kern gibt die
# Sperre mit dem Prozess frei, auch nach kill -9.
DATEI_SPERRE = PDATA / "dienst.sperre"
_SPERRE_FD = None


def einzelsperre_nehmen() -> bool:
    """True: dieser Prozess ist der einzige Dienst (oder es gibt kein flock).
    False: ein anderer Dienst haelt die Sperre."""
    global _SPERRE_FD
    try:
        import fcntl
    except ImportError:
        return True
    try:
        PDATA.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(DATEI_SPERRE), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as err:
        _LOG.warning("Sperrdatei %s nicht anlegbar (%s) - der Dienst laeuft ohne "
                     "Einzelsperre.", DATEI_SPERRE, err)
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    _SPERRE_FD = fd
    return True


SCHALTER_ERLAUBT = ("--einmal", "--selbsttest", "--mqtt-leeren", "--signal-test")


def main() -> int:
    # WEISSLISTE DER SCHALTER (C8, Durchgang 02.10.2026). Bis 0.9.26 lief
    # jeder andere Schalter als Dauerdienst - "--selftest" (Tippfehler)
    # arbeitete unsichtbar die Warteschlange mit ab und fragte die Cloud,
    # ohne dass Waechter oder "stop" ihn sahen (gemessen, Codebericht Nr. 10).
    fremd = [a for a in sys.argv[1:] if a not in SCHALTER_ERLAUBT]
    if fremd:
        sys.stderr.write("FEHLER: unbekannter Schalter %s. Erlaubt sind %s, oder gar "
                         "keiner (Dauerdienst). Es wurde nichts gestartet.\n"
                         % (" ".join(fremd), ", ".join(SCHALTER_ERLAUBT)))
        return 2
    # VOR log_einrichten(): --mqtt-leeren laeuft aus der Deinstallation, und
    # dort soll kein Protokoll mehr entstehen.
    if "--mqtt-leeren" in sys.argv:
        return mqtt_leeren()
    # Auch --selbsttest steht VOR log_einrichten() (seit 0.9.24): der
    # Selbsttest gibt seine Zeilen auf die Ausgabe und schreibt keine
    # Protokollzeile, log_einrichten() legte aber log/plugins/<ordner> an.
    # Aus einem Pruefarchiv unter <Wurzel>/pruefung/<ordner>/bin heraus
    # entstand damit ein Ordner in der LAUFENDEN Installation - in WSL
    # gemessen (Pruefung-VolkswagenID-0.9.24, Fall Y1).
    if "--selbsttest" in sys.argv:
        return selbsttest()
    # Gruppe 41: Testmeldung an SignalBot (Knopf im Reiter Einstellungen). Ohne Protokoll, ohne Sperre -
    # er laeuft neben dem Dienst; eine Zeile fuer die Oberflaeche (vw_signal_test_lesen()).
    if "--signal-test" in sys.argv:
        e, text = signal_test()
        print("SIGNAL_TEST;ERGEBNIS=%s;TEXT=%s" % (e, text))
        return 0 if e == "angenommen" else 1
    log_einrichten()
    # Vor allem anderen, was schreibt (Abbild, Zustand, Sollmerker).
    if not einzelsperre_nehmen():
        meldung = ("Ein anderer Abrufdienst dieses Plugins laeuft schon (Sperre %s). "
                   "Dieser Aufruf endet, ohne etwas zu tun." % DATEI_SPERRE)
        sys.stderr.write("FEHLER: " + meldung + "\n")
        _LOG.warning(meldung)
        return 3
    signal.signal(signal.SIGTERM, signal_behandeln)
    signal.signal(signal.SIGINT, signal_behandeln)
    try:
        return dienst(einmal="--einmal" in sys.argv)
    except KeyboardInterrupt:
        return 0
    except Exception as err:  # noqa: BLE001
        _LOG.error("Dienst abgebrochen: %s", fehlertext(err))
        zustand_schreiben(ok=0, fehler=fehlertext(err))
        return 1


if __name__ == "__main__":
    sys.exit(main())
