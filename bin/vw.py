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


def mqtt_wert_saeubern(wert):
    """Einen Wert fuer den UDP-Eingang des MQTT-Gateways unschaedlich machen.

    Das Gateway liest zeilenweise. Ein Zeilenumbruch im Wert zerlegt die
    Uebertragung, und aus den Bruchstuecken bildet das Gateway erfundene
    Themen. Ein Tabulator schadet ebenso, weil Leerzeichen Thema und Wert
    trennt.
    """
    text = str(wert)
    for zeichen in ("\r\n", "\r", "\n", "\t"):
        text = text.replace(zeichen, " ")
    while "  " in text:
        text = text.replace("  ", " ")
    return text.strip()


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
if len(SELF.parents) >= 3:
    LBHOME = SELF.parents[2]
else:
    LBHOME = Path(os.environ.get("LBHOMEDIR") or lb_wurzel_ermitteln())
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

# Muessen zu vw_vorgaben() in webfrontend/html/vw_lib.php passen.
#
# Der Takt hat eine harte Untergrenze von 180 Sekunden: der Connector wirft
# darunter beim Anlegen einen ValueError ("Intervall must be at least 180
# seconds"). Der Wert wird deshalb schon hier gekappt, nicht erst dort.
VORGABEN = {
    "intervall": 300,
    "takt_wartung": 12,
    "mqtt_ein": 0,
    "mqtt_topic": "volkswagen",
    "steuerung_ein": 0,
    "temp_min": 16,
    "temp_max": 29,
    "verlauf_tage": 8,
    "zugriff_erzwingen": 0,
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
    PLOG.mkdir(parents=True, exist_ok=True)
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


def config() -> dict:
    c = dict(VORGABEN)
    c.update(json_lesen(DATEI_CONFIG))
    c["intervall"] = max(TAKT_MIN, min(3600, ganz(c.get("intervall"), 300)))
    c["takt_wartung"] = max(1, min(240, ganz(c.get("takt_wartung"), 12)))
    c["temp_min"] = max(10, min(30, ganz(c.get("temp_min"), 16)))
    c["temp_max"] = max(10, min(30, ganz(c.get("temp_max"), 29)))
    if c["temp_min"] > c["temp_max"]:
        c["temp_min"], c["temp_max"] = c["temp_max"], c["temp_min"]
    c["verlauf_tage"] = max(1, min(90, ganz(c.get("verlauf_tage"), 8)))
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
    }


def mqtt_senden(paare: dict, praefix: str) -> None:
    z = mqtt_zustand()
    if not z["udpport"]:
        melde_gebremst("mqtt_kein_port",
                       "MQTT: kein UDP-Eingangsport in general.json gefunden - nichts gesendet.")
        return
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
        return
    try:
        for k, v in paare.items():
            if v is None:
                continue
            s.sendto(f"publish {praefix}/{k} {mqtt_wert_saeubern(v)}".encode("utf-8"), ("127.0.0.1", z["udpport"]))
    except OSError as err:
        melde_gebremst("mqtt_senden", f"MQTT: Senden fehlgeschlagen ({err}).")
    finally:
        s.close()


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


def abbild_stamm(fahrzeug) -> dict:
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


def abbild_status(fahrzeug) -> dict:
    from carconnectivity.units import Temperature
    tueren = getattr(fahrzeug, "doors", None)
    klima = getattr(fahrzeug, "climatization", None)
    einst = getattr(klima, "settings", None)
    return {
        "zustand": kennzahl(getattr(fahrzeug, "state", None), FAHRZEUGZUSTAND),
        "zustand_text": etext(getattr(fahrzeug, "state", None)),
        "erreichbar": kennzahl(getattr(fahrzeug, "connection_state", None), ERREICHBAR),
        "verriegelt": kennzahl(getattr(tueren, "lock_state", None), VERRIEGELT),
        "tueren_offen": kennzahl(getattr(tueren, "open_state", None), OFFEN_ZU),
        "fenster_offen": kennzahl(getattr(getattr(fahrzeug, "windows", None), "open_state", None), OFFEN_ZU),
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
    }


def abbild_reichweite(fahrzeug) -> dict:
    from carconnectivity.units import Length, Level, Energy
    a = antriebe(fahrzeug)
    e, v = a["elektro"], a["verbrenner"]
    return {
        "reichweite_km": wert(getattr(getattr(fahrzeug, "drives", None), "total_range", None),
                              Length.KM, 0),
        "anzahl_antriebe": a["anzahl"],
        "soc": wert(getattr(e, "level", None), Level.PERCENTAGE, 0),
        "reichweite_elektro_km": wert(getattr(e, "range", None), Length.KM, 0),
        "batterie_kwh": wert(getattr(getattr(e, "battery", None), "total_capacity", None),
                             Energy.KWH, 1),
        "tank_prozent": wert(getattr(v, "level", None), Level.PERCENTAGE, 0),
        "reichweite_verbrenner_km": wert(getattr(v, "range", None), Length.KM, 0),
        "oelstand_prozent": wert(getattr(v, "oil_level", None), Level.PERCENTAGE, 0),
    }


def abbild_laden(fahrzeug) -> dict:
    from carconnectivity.units import Power, Speed, Level, Current
    laden = getattr(fahrzeug, "charging", None)
    einst = getattr(laden, "settings", None)
    stecker = getattr(laden, "connector", None)
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
    }


def abbild_wartung(fahrzeug) -> dict:
    from carconnectivity.units import Length
    w = getattr(fahrzeug, "maintenance", None)
    return {
        "inspektion_tage": tage_bis(getattr(w, "inspection_due_at", None)),
        "inspektion_km": wert(getattr(w, "inspection_due_after", None), Length.KM, 0),
        "oelservice_tage": tage_bis(getattr(w, "oil_service_due_at", None)),
        "oelservice_km": wert(getattr(w, "oil_service_due_after", None), Length.KM, 0),
    }


def abbild_position(fahrzeug) -> dict:
    p = getattr(fahrzeug, "position", None)
    ort = getattr(p, "location", None)
    strasse = " ".join(x for x in (str(wert(getattr(ort, "road", None)) or ""),
                                   str(wert(getattr(ort, "house_number", None)) or "")) if x)
    stadt = str(wert(getattr(ort, "city", None)) or "")
    return {
        "breite": wert(getattr(p, "latitude", None), None, 6),
        "laenge": wert(getattr(p, "longitude", None), None, 6),
        "positionsart": etext(getattr(p, "position_type", None)),
        "strasse": strasse,
        "ort": stadt,
        "adresse": ", ".join(x for x in (strasse, stadt) if x),
    }


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
            d.update(funktion(fahrzeug))
        except Exception as err:  # noqa: BLE001
            ausfaelle[name] = fehlertext(err)
            melde_gebremst(f"ab_{name}", f"Abschnitt '{name}' konnte nicht gelesen werden: "
                                         f"{ausfaelle[name]}", 900)
    if "wartung" not in [n for n, _ in abschnitte]:
        # Zwischen zwei Wartungsabrufen den letzten bekannten Stand weiterreichen.
        for k, v in stamm.items():
            if k.startswith(("inspektion", "oelservice")):
                d.setdefault(k, v)
    d["ausfaelle"] = ausfaelle
    d["ok"] = 0 if len(ausfaelle) >= 3 else 1
    return d


# ---------------------------------------------------------------------------
# Verlauf (Ladezustand beziehungsweise Tankfuellstand ueber den Tag)
# ---------------------------------------------------------------------------
def verlauf_anhaengen(nummer: int, stand, reichweite, tage: int) -> None:
    if stand is None:
        return
    ordner = PDATA / "verlauf"
    ordner.mkdir(parents=True, exist_ok=True)
    datei = ordner / f"fahrzeug{nummer}_{time.strftime('%Y%m%d')}.csv"
    marke = PDATA / f".verlauf_ts_{nummer}"
    letzte = 0
    try:
        letzte = int(marke.read_text())
    except (OSError, ValueError):
        pass
    if time.time() - letzte < 240:
        return
    try:
        with datei.open("a", encoding="utf-8") as f:
            f.write(f"{int(time.time())};{stand};{reichweite if reichweite is not None else ''}\n")
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
# Schreibbefehle aus der Warteschlange
#
# Der Loxone-Endpunkt legt hier eine JSON-Datei ab, der Dienst arbeitet sie ab
# und legt die Antwort daneben. Der Endpunkt selbst spricht NIE mit Volkswagen.
# ---------------------------------------------------------------------------
def antwort_schreiben(kennung: str, ok: int, meldung: str, zusatz: dict | None = None) -> None:
    ORDNER_ANTWORTEN.mkdir(parents=True, exist_ok=True)
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
    """Nimmt entweder die laufende Nummer (1-basiert) oder die Fahrgestellnummer."""
    s = str(nummer_oder_vin or "").strip()
    for f in fahrzeuge:
        v = str(wert(getattr(f, "vin", None)) or "")
        if v and v.upper() == s.upper():
            return f
    try:
        n = int(s)
    except (TypeError, ValueError):
        n = 1
    return fahrzeuge[n - 1] if 1 <= n <= len(fahrzeuge) else None


def befehl_holen(objekt, name: str):
    """Holt einen Befehl aus einem Befehlsverzeichnis, oder None."""
    cmds = getattr(objekt, "commands", None)
    verzeichnis = getattr(cmds, "commands", None) or {}
    return verzeichnis.get(name)


def befehl_ausfuehren(fahrzeuge: list, cfg: dict, b: dict) -> tuple[int, str, dict]:
    """Rueckgabe: (ok, Meldung, Zusatzfelder). ok = 1 angenommen, 0 abgelehnt.

    Was "angenommen" heisst: die Bibliothek setzt den Befehl als HTTP-Anfrage
    an den Volkswagen-Server ab und wirft, wenn der nicht mit 200 antwortet.
    ok = 1 bedeutet also: der Server hat den Auftrag entgegengenommen. Ob das
    Fahrzeug ihn ausfuehrt, zeigt erst der naechste Abruf - das steht auch so
    in jeder Antwort.
    """
    aktion = str(b.get("aktion") or "")

    if aktion == "abruf":
        return (1, "Sofortabruf eingeplant.", {})

    if not cfg.get("steuerung_ein"):
        return (0, "Die Steuerung ist ausgeschaltet. Reiter Einstellungen, "
                   "Haken 'Schreibende Befehle zulassen'.", {})

    if not fahrzeuge:
        return (0, "Es ist noch kein Fahrzeug bekannt. Erst einen Abruf abwarten.", {})

    f = fahrzeug_waehlen(fahrzeuge, b.get("fahrzeug"))
    if f is None:
        return (0, f"Fahrzeug '{b.get('fahrzeug')}' gibt es nicht. "
                   f"Bekannt sind {len(fahrzeuge)} Fahrzeuge.", {})
    vin = str(wert(getattr(f, "vin", None)) or "")
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
        if p < 10 or p > 100:
            return (0, f"{p} % ist keine zulaessige Ladegrenze. Zulaessig sind 10 bis 100 %.", {})
        einst = getattr(getattr(f, "charging", None), "settings", None)
        attr = getattr(einst, "target_level", None)
        if attr is None:
            return fehlt("Ladegrenze")
        attr.value = float(p)
        return (1, f"Ladegrenze {p} % gesetzt (Volkswagen rundet auf die Stufen, die das "
                   f"Fahrzeug kennt - meist Zehnerschritte)." + nachsatz,
                {"prozent": p, "vin": vin})

    if aktion == "ladestrom":
        a = wert_zahl(b.get("ampere"))
        if a is None:
            return (0, "Der Ampere-Wert fehlt oder ist keine Zahl.", {})
        if int(a) not in LADESTROM_STUFEN:
            return (0, f"{a} A ist keine zulaessige Stufe. Zulaessig sind: "
                       f"{', '.join(str(x) for x in LADESTROM_STUFEN)} A. "
                       f"Welche davon Ihr Fahrzeug kennt, haengt vom Modell ab: manche fuehren "
                       f"den Strom in Ampere (5/10/13/32), die uebrigen kennen nur reduziert (6) "
                       f"und maximal (16).", {})
        einst = getattr(getattr(f, "charging", None), "settings", None)
        attr = getattr(einst, "maximum_current", None)
        if attr is None:
            return fehlt("Ladestrom")
        attr.value = float(int(a))
        return (1, f"Ladestrom {int(a)} A gesetzt." + nachsatz, {"ampere": int(a), "vin": vin})

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


def warteschlange(fahrzeuge: list, cfg: dict) -> bool:
    """Arbeitet alle vorliegenden Befehle ab. True, wenn ein Sofortabruf
    angefordert wurde."""
    ORDNER_BEFEHLE.mkdir(parents=True, exist_ok=True)
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
MQTT_FELDER = (
    "soc", "tank_prozent", "reichweite_km", "kilometerstand", "verriegelt",
    "tueren_offen", "fenster_offen", "licht_an", "handbremse", "zustand",
    "erreichbar", "klima_an", "zieltemperatur", "aussentemperatur",
    "scheibenheizung", "laedt", "ladeleistung_kw", "ladetempo_kmh",
    "ladegrenze", "ladestrom_a", "kabel_verbunden", "stecker_verriegelt",
    "laden_fertig_um", "breite", "laenge", "inspektion_tage", "inspektion_km",
    "oelservice_tage", "oelservice_km",
)


def abbild_schreiben(stand: dict, cfg: dict, ok: int, fehler: str = "") -> dict:
    """Schreibt den Zwischenspeicher.

    Bei einem fehlgeschlagenen Abruf bleiben die zuletzt gueltigen Werte
    stehen, und der Zeitstempel wird NICHT aufgefrischt. Beides mit Absicht:
    sonst meldete der Endpunkt ploetzlich FAHRZEUG_UNBEKANNT, obwohl nur eine
    Anfrage schiefging, und ALTER bliebe klein - woran aber die
    Ausfallerkennung in Loxone haengt.
    """
    fahrzeuge = stand.get("fahrzeuge") or {}
    lox = {
        "ok": ok,
        "fehler": fehler,
        "letzter_versuch": int(time.time()),
        "anzahl_fahrzeuge": len(fahrzeuge),
        "fahrzeuge": fahrzeuge,
    }
    if stand.get("ts"):
        lox["ts"] = int(stand["ts"])
    json_schreiben(DATEI_LOXONE, lox)
    json_schreiben(DATEI_CACHE, {"ts": int(time.time()), "ok": ok,
                                 "fehler": fehler, "fahrzeuge": fahrzeuge})

    praefix = str(cfg.get("mqtt_topic") or "volkswagen").strip("/") or "volkswagen"
    if not ok:
        # Bei einer Stoerung nur das ok-Thema senden. Die alten Messwerte
        # erneut zu veroeffentlichen liesse sie frisch aussehen.
        if cfg.get("mqtt_ein"):
            mqtt_senden({"ok": 0}, praefix)
        return lox

    for nummer, f in fahrzeuge.items():
        try:
            verlauf_anhaengen(int(nummer),
                              f.get("soc") if f.get("soc") is not None else f.get("tank_prozent"),
                              f.get("reichweite_km"), cfg["verlauf_tage"])
        except (TypeError, ValueError):
            pass

    if cfg.get("mqtt_ein"):
        paare = {"ok": ok, "fahrzeuge": len(fahrzeuge)}
        for nummer, f in fahrzeuge.items():
            for feld in MQTT_FELDER:
                paare[f"fahrzeug{nummer}/{feld}"] = f.get(feld)
        mqtt_senden(paare, praefix)

    return lox


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


class Zeitgrenze:
    """Bricht einen haengenden Aufruf nach $sekunden ab."""

    def __init__(self, sekunden: int, was: str = "Abruf"):
        self.sekunden = int(sekunden)
        self.was = was
        self.alt = None

    def __enter__(self):
        if self.sekunden > 0 and threading.current_thread() is threading.main_thread():
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
        _LOG.error("Zugangsdaten fehlen. Reiter Einstellungen der Plugin-Oberflaeche oeffnen.")
        zustand_schreiben(ok=0, fehler="Zugangsdaten fehlen.")
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
    fehler_folge = 0
    stammdaten: dict[str, dict] = {}

    try:
        while _LAUF:
            cfg = config()  # Aenderungen aus der Oberflaeche ohne Neustart uebernehmen
            ok = 0
            fehler = ""
            fahrzeuge: dict[str, dict] = {}
            liste: list = []
            try:
                # Mit Wecker: ohne ihn haelt ein Server, der die Verbindung
                # annimmt und dann schweigt, den ganzen Dienst an - samt
                # Befehlswarteschlange, die im selben Ablauf abgearbeitet wird.
                with Zeitgrenze(GRENZE_ABRUF, "Der Abruf bei Volkswagen"):
                    cc.fetch_all()
                garage = cc.get_garage()
                liste = list(garage.list_vehicles()) if garage is not None else []
                liste.sort(key=lambda f: str(wert(getattr(f, "vin", None)) or ""))
                for i, f in enumerate(liste, start=1):
                    vin = str(wert(getattr(f, "vin", None)) or str(i))
                    stammdaten.setdefault(vin, {})
                    abbild = fahrzeug_abbilden(f, cfg, zyklus, stammdaten[vin])
                    for k, v in abbild.items():
                        if k.startswith(("inspektion", "oelservice")) and v is not None:
                            stammdaten[vin][k] = v
                    fahrzeuge[str(i)] = abbild
                ok = 1 if fahrzeuge and any(x.get("ok") for x in fahrzeuge.values()) else 0
                if not liste:
                    fehler = "Das Konto fuehrt kein Fahrzeug."
                fehler_folge = 0 if ok else fehler_folge + 1
            except Exception as err:  # noqa: BLE001
                fehler = fehlertext(err)
                fehler_folge += 1
                melde_gebremst("abruf", f"Abruf fehlgeschlagen: {fehler}", 900)

            if ok and fahrzeuge:
                stand = {"ts": int(time.time()), "fahrzeuge": fahrzeuge}
            abbild_schreiben(stand, cfg, ok, fehler)
            zustand_schreiben(ok=ok, fehler=fehler, zyklus=zyklus, fehler_folge=fehler_folge,
                              pid=os.getpid(), intervall=cfg["intervall"],
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
                    if warteschlange(liste, cfg):
                        break  # Sofortabruf angefordert
                except Exception as err:  # noqa: BLE001
                    _LOG.error("Warteschlange: %s", fehlertext(err))
                time.sleep(1)
                rest -= 1
    finally:
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
    else:
        zeilen.append("[INFO] Keine S-PIN hinterlegt (nur fuer Ver- und Entriegeln noetig, "
                      "das dieses Plugin nicht anbietet)")

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

    c = config()
    zeilen.append(f"[INFO] Takt {c['intervall']} s (Untergrenze der Bibliothek: {TAKT_MIN} s), "
                  f"Wartung alle {c['takt_wartung']} Takte")
    zeilen.append(f"[INFO] Schreibende Befehle: "
                  f"{'zugelassen' if c.get('steuerung_ein') else 'gesperrt'}, "
                  f"Zieltemperatur erlaubt von {c['temp_min']} bis {c['temp_max']} Grad")

    m = mqtt_zustand()
    if not m["gefunden"]:
        zeilen.append("[FEHL] Im general.json des LoxBerry ist kein MQTT-Abschnitt zu finden")
        fehler += 1
    elif m["autostart"]:
        zeilen.append(f"[OK]   MQTT-Gateway auf Autostart, Broker {m['broker']}:{m['brokerport']}, "
                      f"UDP-Eingang {m['udpport']}")
    else:
        zeilen.append("[FEHL] Das MQTT-Gateway ist nicht auf Autostart gestellt "
                      "(System -> MQTT Gateway). Ohne das kommt am Miniserver nichts an.")
        fehler += 1

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
