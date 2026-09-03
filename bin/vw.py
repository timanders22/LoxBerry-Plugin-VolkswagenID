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
import os
import signal
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


def lb_wurzel_ermitteln():
    """Den LoxBerry-Wurzelordner ohne festen Systempfad bestimmen.

    Vom eigenen Ablageort aufwaerts, bis ein Verzeichnis gefunden ist, das
    config/plugins UND webfrontend enthaelt. Trifft die uebliche
    Installation genauso wie eine an einem anderen Ort.
    """
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isdir(os.path.join(d, "config", "plugins")) \
                and os.path.isdir(os.path.join(d, "webfrontend")):
            return d
        eltern = os.path.dirname(d)
        if eltern == d:
            break
        d = eltern
    return ""


MQTT_MAX = 200


def mqtt_wert_saeubern(wert, laenge: int = MQTT_MAX):
    """Einen Wert fuer den UDP-Eingang des MQTT-Gateways unschaedlich machen.

    Das Gateway liest zeilenweise. Ein Zeilenumbruch im Wert zerlegt die
    Uebertragung, und aus den Bruchstuecken bildet das Gateway erfundene
    Themen. Ein Tabulator schadet ebenso, weil Leerzeichen Thema und Wert
    trennt.

    Gekappt wird ab 0.9.10, weil seither auch Text hinausgeht: eine Anschrift
    oder ein Fehlertext hat keine Obergrenze, ein UDP-Datagramm schon. 200
    Zeichen sind aus MGiSmart uebernommen und NICHT am Gateway nachgemessen.
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
PNAME = SELF.name


def _ist_lbhome(p: Path) -> bool:
    """Sieht dieses Verzeichnis wie ein LoxBerry aus?"""
    try:
        return (p / "config" / "plugins").is_dir() and (p / "webfrontend").is_dir()
    except OSError:
        return False


# Die drei Ebenen aufwaerts sind der Normalfall - aber sie werden GEPRUEFT.
#
# Bis 0.9.9 stand hier `if len(SELF.parents) >= 3`, und das ist bei jedem
# absoluten Pfad erfuellt: der Rueckfallweg wurde nie genommen, und
# lb_wurzel_ermitteln() war toter Code. Aus einem entpackten Archiv heraus
# ergab das LBHOME = <Desktop> und PNAME = "bin" - der Selbsttest meldete
# dann drei nicht beschreibbare Ordner statt zu sagen, dass das Plugin gar
# nicht installiert ist. Gemessen am 27.08.2026.
LBHOME = SELF.parents[2] if len(SELF.parents) >= 3 and _ist_lbhome(SELF.parents[2]) else None
if LBHOME is None:
    _ersatz = os.environ.get("LBHOMEDIR") or lb_wurzel_ermitteln()
    LBHOME = Path(_ersatz) if _ersatz else SELF.parents[min(2, len(SELF.parents) - 1)]
    NICHT_INSTALLIERT = not _ist_lbhome(LBHOME)
else:
    NICHT_INSTALLIERT = False
PDATA = LBHOME / "data" / "plugins" / PNAME
PLOG = LBHOME / "log" / "plugins" / PNAME
PCONFIG = LBHOME / "config" / "plugins" / PNAME

DATEI_CONFIG = PCONFIG / "vw.json"
DATEI_ZUGANG = PCONFIG / "zugang.json"
DATEI_CACHE = PDATA / "cache.json"
DATEI_LOXONE = PDATA / "loxone.json"
DATEI_ZUSTAND = PDATA / "zustand.json"
DATEI_TOKEN = PDATA / "token.json"          # Anmeldemarken der Bibliothek
DATEI_ZWISCHEN = PDATA / "bibliothek_cache.json"
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
}
GRENZEN_SCHALT = ("mqtt_ein", "mqtt_retain", "steuerung_ein", "eingreifend_ein",
                  "zugriff_erzwingen", "empf_kleiner", "abfahrt_ein")

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
_LETZTE_MELDUNG: dict[str, float] = {}


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
        h = RotatingFileHandler(DATEI_LOG, maxBytes=512000, backupCount=1, encoding="utf-8")
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


def json_schreiben(pfad: Path, daten, rechte: int | None = None) -> bool:
    """Erst in eine Nebendatei, dann umbenennen. So liest die Oberflaeche nie
    eine halb geschriebene Datei."""
    try:
        pfad.parent.mkdir(parents=True, exist_ok=True)
        tmp = pfad.with_suffix(pfad.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(daten, f, ensure_ascii=False, indent=1, default=str)
        if rechte is not None:
            os.chmod(tmp, rechte)
        os.replace(tmp, pfad)
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


def config() -> dict:
    """Die Konfiguration - vollstaendig und geprueft.

    Jeder Wert wird gegen dieselben Grenzen gehalten, die auch das Formular
    benutzt. Eine Datei kann von Hand geschrieben, aus einer Sicherung
    zurueckgespielt oder aus einer aelteren Fassung uebernommen sein; geprueft
    wird deshalb an BEIDEN Enden, nicht nur beim Speichern.
    """
    c = dict(VORGABEN)
    c.update(json_lesen(DATEI_CONFIG))
    for name, (lo, hi) in GRENZEN_GANZ.items():
        c[name] = max(lo, min(hi, ganz(c.get(name), VORGABEN[name])))
    for name in GRENZEN_SCHALT:
        c[name] = schalt(c.get(name), VORGABEN[name])
    if c["temp_min"] > c["temp_max"]:
        c["temp_min"], c["temp_max"] = c["temp_max"], c["temp_min"]
    c["intervall"] = max(TAKT_MIN, c["intervall"])
    # Heimatort: leer bleibt leer. Eine 0/0 waere ein Punkt im Atlantik, und
    # jede Entfernungsangabe daraus waere eine Zahl, die richtig aussieht.
    for name, lo, hi in (("heim_breite", -90, 90), ("heim_laenge", -180, 180)):
        v = komma(c.get(name))
        c[name] = v if (v is not None and lo <= v <= hi) else ""
    c["empf_grenze"] = komma(c.get("empf_grenze"))
    if c["empf_grenze"] is None:
        c["empf_grenze"] = ""
    for name in ("mqtt_topic", "empf_thema", "abfahrt_thema"):
        c[name] = str(c.get(name) or "").strip()
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
    Das Befehlswort 'retain' des UDP-Eingangs ist im Bestand dreifach belegt
    (Gardena, Intercom, WOLF ISM NG); an einem Gateway NACHGEMESSEN wurde es
    hier nicht.

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
                     kwh, km, quelle: str) -> None:
    """Einen abgeschlossenen Ladevorgang protokollieren."""
    try:
        neu = not DATEI_LADUNGEN.exists()
        with DATEI_LADUNGEN.open("a", encoding="utf-8") as f:
            if neu:
                f.write("# fahrzeug;start;ende;soc_vor;soc_nach;kwh;km;quelle\n")
            f.write("%d;%d;%d;%s;%s;%s;%s;%s\n" % (
                nummer, start, ende,
                "" if soc_vor is None else soc_vor,
                "" if soc_nach is None else soc_nach,
                "" if kwh is None else round(kwh, 2),
                "" if km is None else km,
                mqtt_wert_saeubern(quelle, 40).replace(";", " ")))
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
            # ein Seitenaufruf las ein Bruchstueck.
            tmp = DATEI_LADUNGEN.with_suffix(DATEI_LADUNGEN.suffix + ".tmp")
            tmp.write_text("\n".join(kopf + rest) + "\n", encoding="utf-8")
            os.replace(tmp, DATEI_LADUNGEN)
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
    elif laedt == 0 and war == 1 and v.get("lade_start"):
        vor = v.get("lade_soc")
        kwh = None
        if vor is not None and soc is not None and kapazitaet:
            d = float(soc) - float(vor)
            # Ein Ladevorgang, bei dem der Stand faellt, ist keiner. Das kommt
            # vor: das Fahrzeug meldet "charging", waehrend es die Klimaanlage
            # aus der Batterie speist.
            kwh = round(d / 100.0 * float(kapazitaet), 2) if d > 0 else None
        ladung_anhaengen(nummer, int(v["lade_start"]), jetzt, vor, soc, kwh,
                         km, f.get("ladesaeule_name") or f.get("ladeart") or "")
        v.pop("lade_start", None)
        v.pop("lade_soc", None)
        v.pop("lade_km", None)
    v["laedt"] = laedt

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


def fahrzeug_waehlen(fahrzeuge: list, nummer_oder_vin):
    """Nimmt entweder die laufende Nummer (1-basiert) oder die Fahrgestellnummer.

    Abgewiesen, nicht zurechtgebogen. Bis 0.9.9 fing das except jede
    Zeichenkette ab und setzte n = 1 - eine VIN mit EINEM falschen Zeichen
    ergab damit Fahrzeug 1. Bei zwei Fahrzeugen startete
    'klima_start&fahrzeug=<VIN mit Tippfehler>' die Klimatisierung am falschen
    Auto und meldete OK=1. Gemessen am 27.08.2026; der lesende Weg am Endpunkt
    hat es immer richtig gemacht.

    Leer oder gar nicht angegeben bleibt Fahrzeug 1 - das ist die Vorgabe,
    nicht ein missratener Wert.
    """
    s = str(nummer_oder_vin or "").strip()
    if s == "":
        return fahrzeuge[0] if fahrzeuge else None
    for f in fahrzeuge:
        v = str(wert(getattr(f, "vin", None)) or "")
        if v and v.upper() == s.upper():
            return f
    if not s.isdigit():
        return None
    n = int(s)
    return fahrzeuge[n - 1] if 1 <= n <= len(fahrzeuge) else None


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
# Ob der Volkswagen-Connector fuer jede einen Schreibhaken registriert, ist
# UNGEMESSEN - hier liegt kein Fahrzeug und der Connector ist nicht
# installiert. Fehlt der Haken, wirft die Bibliothek, und die Antwort sagt es.
SCHALTER = {
    "sitzheizung":      ("climatization", "seat_heating"),
    "klima_entriegeln": ("climatization", "climatization_at_unlock"),
    "klima_ohne_netz":  ("climatization", "climatization_without_external_power"),
    "stecker_auto":     ("charging", "auto_unlock"),
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
                   f"Bekannt sind {len(fahrzeuge)} Fahrzeuge. Angegeben werden koennen die "
                   f"laufende Nummer oder die vollstaendige Fahrgestellnummer.", {})
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
        try:
            datei.unlink()
        except OSError:
            pass
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
MQTT_OHNE_RETAIN = frozenset((
    # Lebenszeichen - nie behalten
    "ts", "zaehler",
    # Leistung und Tempo
    "ladeleistung_kw", "ladetempo_kmh", "ladesaeule_kw",
    # Zeitpunkte und Restzeiten
    "laden_fertig_um", "klima_fertig_um", "standzeit_min", "ladung_vor_stunden",
    # Temperaturen
    "aussentemperatur", "batterie_temp",
))


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


def abbild_schreiben(stand: dict, cfg: dict, ok: int, fehler: str = "",
                     grund: str = "", zaehler: int = -1, fehler_folge: int = 0) -> dict:
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
    json_schreiben(DATEI_LOXONE, lox)
    json_schreiben(DATEI_CACHE, {"letzter_versuch": int(time.time()), "ok": ok,
                                 "fehler": fehler, "fahrzeuge": fahrzeuge})

    praefix = str(cfg.get("mqtt_topic") or "volkswagen").strip("/") or "volkswagen"
    retain = 1 if cfg.get("mqtt_retain") else 0

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
        if cfg.get("mqtt_ein"):
            versucht, schlecht = mqtt_senden(oben, praefix, retain)
            lox["mqtt_versucht"], lox["mqtt_schlecht"] = versucht, schlecht
            json_schreiben(DATEI_LOXONE, lox)
        return lox

    for nummer, f in fahrzeuge.items():
        try:
            verlauf_anhaengen(int(nummer),
                              f.get("soc") if f.get("soc") is not None else f.get("tank_prozent"),
                              f.get("reichweite_km"), f.get("kilometerstand"),
                              cfg["verlauf_tage"])
        except (TypeError, ValueError):
            pass

    if cfg.get("mqtt_ein"):
        paare = dict(oben)
        for nummer, f in fahrzeuge.items():
            for feld in MQTT_FELDER:
                paare[f"fahrzeug{nummer}/{feld}"] = f.get(feld)
            for feld in MQTT_TEXTFELDER:
                w = f.get(feld)
                # Ein leeres Textfeld wird NICHT gesendet: mit Retain loeschte
                # eine leere Nutzlast das behaltene Thema. mqtt_senden() faengt
                # das ebenfalls ab; hier steht es, damit die Absicht sichtbar
                # bleibt.
                if w is not None and str(w).strip() != "":
                    paare[f"fahrzeug{nummer}/{feld}"] = w
        versucht, schlecht = mqtt_senden(paare, praefix, retain)
        lox["mqtt_versucht"], lox["mqtt_schlecht"] = versucht, schlecht
        json_schreiben(DATEI_LOXONE, lox)

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


class Horcher:
    """Haelt eine MQTT-Verbindung und merkt sich die letzten Werte."""

    def __init__(self):
        self.werte: dict[str, str] = {}
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
        if cfg.get("empf_thema"):
            t.append(str(cfg["empf_thema"]))
        if cfg.get("abfahrt_ein") and cfg.get("abfahrt_thema"):
            t.append(str(cfg["abfahrt_thema"]))
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
                    self.werte[nachricht.topic] = nachricht.payload.decode("utf-8", "replace")
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
            try:
                k = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)   # paho 2.x
            except (AttributeError, TypeError):
                k = mqtt.Client()                                    # paho 1.x
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

    def zahl(self, thema: str):
        return komma(self.werte.get(thema))


_HORCHER = Horcher()


def ladeempfehlung(cfg: dict) -> int | None:
    """1 = laden empfohlen, 0 = nicht, None = keine Aussage.

    Keine Aussage ist ausdruecklich ein dritter Ausgang: ohne eingestelltes
    Thema, ohne Grenze oder ohne empfangenen Wert wird nichts behauptet.
    """
    thema = str(cfg.get("empf_thema") or "")
    grenze = komma(cfg.get("empf_grenze"))
    if thema == "" or grenze is None:
        return None
    v = _HORCHER.zahl(thema)
    if v is None:
        return None
    return 1 if ((v <= grenze) if cfg.get("empf_kleiner") else (v >= grenze)) else 0


def abfahrt_pruefen(cfg: dict, fahrzeuge: list) -> None:
    """Klimatisierung vor der naechsten Abfahrt anfordern - einmal je Abfahrt.

    Das Thema des Abfahrts-Assistenten fuehrt die Restzeit in Minuten. Faellt
    sie unter den Vorlauf, wird EINMAL angefordert; der Merker wird erst
    zurueckgesetzt, wenn die Restzeit wieder ueber dem Vorlauf liegt. Ohne
    diesen Merker liefe die Anforderung im Takt weiter, und Volkswagen sperrt
    ein Konto, das zu oft schreibt.
    """
    if not cfg.get("abfahrt_ein") or not cfg.get("abfahrt_thema"):
        return
    if not cfg.get("steuerung_ein") or not fahrzeuge:
        return
    rest = _HORCHER.zahl(str(cfg["abfahrt_thema"]))
    if rest is None:
        return
    vorlauf = int(cfg.get("abfahrt_vorlauf") or 20)
    global _ABFAHRT_GEMELDET
    if rest > vorlauf:
        _ABFAHRT_GEMELDET = False
        return
    if _ABFAHRT_GEMELDET or rest < 0:
        return
    _ABFAHRT_GEMELDET = True
    ok, meldung, _ = befehl_ausfuehren(fahrzeuge, cfg, {
        "aktion": "klima_start", "fahrzeug": "1",
        "temp": cfg.get("abfahrt_temp", 21), "von": "abfahrt"})
    _LOG.info("Vorklimatisierung (Abfahrt in %s min, Vorlauf %s min): ok=%s %s",
              rest, vorlauf, ok, meldung)


_ABFAHRT_GEMELDET = False


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
        zustand_schreiben(ok=0, grund="ZUGANG_FEHLT", fehler="Zugangsdaten fehlen.")
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
        zustand_schreiben(ok=0, fehler=meldung)
        return 1
    rechte_sichern()

    stand: dict = {"ts": 0, "fahrzeuge": {}}
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
                stand = {"ts": int(time.time()), "fahrzeuge": fahrzeuge}
            # Der Zaehler laeuft bei JEDEM Durchgang eine Stelle weiter, auch
            # bei einer Stoerung: er beantwortet die Frage "arbeitet der Dienst
            # noch", nicht "war der Abruf erfolgreich". Dafuer gibt es ok.
            zaehler = (zaehler + 1) % 1000
            abbild_schreiben(stand, cfg, ok, fehler, grund, zaehler, fehler_folge)
            zustand_schreiben(ok=ok, fehler=fehler, grund=grund, zyklus=zyklus,
                              zaehler=zaehler, fehler_folge=fehler_folge,
                              pid=os.getpid(), intervall=cfg["intervall"],
                              horcher=1 if _HORCHER.verbunden else 0,
                              horcher_grund=_HORCHER.grund,
                              anzahl_fahrzeuge=len(stand["fahrzeuge"]))
            rechte_sichern()
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
                    abfahrt_pruefen(cfg, liste)
                except Exception as err:  # noqa: BLE001
                    _LOG.error("Warteschlange: %s", fehlertext(err))
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
    # andere Entfernung als Loxone. Muenchen -> Berlin sind rund 504 km.
    _e = entfernung_m(48.1372, 11.5756, 52.5200, 13.4050)
    if _e is not None and 500000 <= _e <= 508000:
        zeilen.append(f"[OK]   Entfernungsrechnung geeicht (Muenchen-Berlin: {_e} m)")
    else:
        fehler += 1
        zeilen.append(f"[FEHL] Die Entfernungsrechnung liefert {_e} m statt rund 504000 m")

    if NICHT_INSTALLIERT:
        zeilen.append(f"[INFO] Dieses Plugin ist NICHT installiert - es laeuft aus einem "
                      f"entpackten Archiv. Die Pfade unten zeigen deshalb neben den Ordner "
                      f"({LBHOME}); das ist kein Fehler, aber auch keine Pruefung der Anlage.")

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


def main() -> int:
    log_einrichten()
    if "--selbsttest" in sys.argv:
        return selbsttest()
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
