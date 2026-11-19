# -*- coding: utf-8 -*-
"""Gemeinsamer Uhrabgleich, Python-Haelfte (ENTWURF 1.0.0).

DIESE DATEI IST IN MEHREREN PLUGINS BYTEWEISE GLEICH. Stammfassung:
Werkzeuge/gemeinsam/uhrabgleich.py im Arbeitsordner; gemeinsam_pruefen.py meldet
jede Abschrift, die abweicht. Geaendert wird nur ergaenzend, mit angehobener
UHR_FASSUNG, und gleichzeitig mit uhrabgleich.php (gleiche Fassungsnummer).

Die Haelfte fuer Dienste in Python: sie liest die Kopfzeile Date einer Antwort
der Cloud und schreibt <datenordner>/uhr.json in DERSELBEN Form wie
uhrabgleich.php (abweichung_s, zeit, quelle, fassung). Die Zeile im Reiter Test
baut immer die PHP-Haelfte (uhr_pruefzeile()); hier gibt es keine Texte.

Wege:
  requests, eigene Sitzung:    sitzung.hooks["response"].append(uhr_requests_haken(ordner))
  requests, einzelne Antwort:  uhr_aus_requests(ordner, antwort)
  requests in einer Bibliothek (Sitzung nicht greifbar, etwa carconnectivity):
                               uhr_requests_ueberall(ordner, ("vwgroup.io", ...))
  aiohttp, eigene Sitzung:     ClientSession(..., trace_configs=[uhr_aiohttp_spur(ordner)])
  sonst:                       uhr_merken(ordner, uhr_datum_lesen(wert), dauer_ms, quelle)

Nichts hier wirft in den Abruf hinein: jeder Weg faengt seine Fehler selbst und
gibt eine Kennung zurueck. Die Antwort wird nie veraendert.

Aufruf: python3 -B uhrabgleich.py --selbsttest
"""
import json
import os
import re
import sys
import time
import calendar

UHR_FASSUNG = "1.0.0"
UHR_GRENZE_S = 30
UHR_SCHREIB_DELTA_S = 2
UHR_SCHREIB_ALTER_S = 600
UHR_DATEI = "uhr.json"
UHR_DAUER_MAX_MS = 600000

_TAGE = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_MONATE = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
           "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
_FORM = re.compile(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun), ([0-9]{2}) (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
                   r"([0-9]{4}) ([0-9]{2}):([0-9]{2}):([0-9]{2}) GMT\Z")
_KOPF = re.compile(r"Date:[ \t]*(.*?)[ \t\r\n]*\Z", re.I | re.S)
_QUELLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}\Z")


def uhr_datum_lesen(wert):
    """IMF-fixdate ("Thu, 19 Nov 2026 10:00:00 GMT") -> Unixzeit, sonst 0."""
    if not isinstance(wert, str):
        return 0
    m = _FORM.match(wert.strip())
    if not m:
        return 0
    tag, mon, jahr = int(m.group(2)), _MONATE[m.group(3)], int(m.group(4))
    h, i, s = int(m.group(5)), int(m.group(6)), int(m.group(7))
    if jahr < 2000 or h > 23 or i > 59 or s > 59:
        return 0
    try:
        t = calendar.timegm((jahr, mon, tag, h, i, s, 0, 0, 0))
        st = time.gmtime(t)
    except (ValueError, OverflowError):
        return 0
    if (st.tm_year, st.tm_mon, st.tm_mday) != (jahr, mon, tag) or _TAGE[st.tm_wday] != m.group(1):
        return 0
    return int(t) if t > 0 else 0


def uhr_kopfzeile(zeile):
    """Eine Kopfzeile "Date: ..." -> Unixzeit, sonst 0."""
    if not isinstance(zeile, str):
        return 0
    m = _KOPF.match(zeile)
    return uhr_datum_lesen(m.group(1)) if m else 0


def uhr_aus_koepfen(koepfe):
    """Date aus einem Kopf-Feld (dict, requests CaseInsensitiveDict, aiohttp CIMultiDict) oder einer
    Liste von Zeilen (Statuszeile HTTP/... setzt zurueck). Rueckgabe Unixzeit oder 0."""
    if koepfe is None:
        return 0
    if isinstance(koepfe, (list, tuple)):
        datum = 0
        for z in koepfe:
            if not isinstance(z, str):
                continue
            if z[:5].upper() == "HTTP/":
                datum = 0
                continue
            t = uhr_kopfzeile(z)
            if t > 0:
                datum = t
        return datum
    try:
        werte = [w for n, w in koepfe.items() if isinstance(n, str) and n.lower() == "date"]
    except Exception:  # noqa: BLE001 - fremdes Objekt: keine Messung
        return 0
    return uhr_datum_lesen(werte[-1]) if werte else 0


def uhr_abweichung(datum, dauer_ms, jetzt):
    """Mitte der Anfrage minus Date; positiv: die Systemuhr geht vor. Rundung wie PHP round()."""
    halb = int(dauer_ms) / 2000.0
    gerundet = int(halb + 0.5) if halb >= 0 else -int(-halb + 0.5)
    return int(jetzt) - gerundet - int(datum)


def uhr_quelle_ok(q):
    return isinstance(q, str) and _QUELLE.match(q) is not None


def uhr_pfad(ordner):
    return os.path.join(str(ordner).rstrip("/\\"), UHR_DATEI)


def _ganz(w):
    return isinstance(w, int) and not isinstance(w, bool)


def uhr_lesen(ordner):
    """None (keine Ablage), False (nicht lesbar/nicht in Form) oder dict wie uhr_lesen() in PHP."""
    if not isinstance(ordner, str) or ordner == "":
        return None
    f = uhr_pfad(ordner)
    if not os.path.isfile(f):
        return None
    try:
        with open(f, "rb") as fh:
            d = json.loads(fh.read().decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    if not isinstance(d, dict) or not _ganz(d.get("abweichung_s")) or not _ganz(d.get("zeit")) or d["zeit"] <= 0:
        return False
    q = d.get("quelle")
    return {"abweichung_s": d["abweichung_s"], "zeit": d["zeit"], "quelle": q if uhr_quelle_ok(q) else ""}


def uhr_merken(ordner, datum, dauer_ms, quelle, jetzt=None, rechte=0o644):
    """Kennung wie in PHP: geschrieben, unveraendert, kein_datum, dauer_ungueltig, quelle_ungueltig,
    kein_ordner, schreibfehler. Wirft nie."""
    if not _ganz(datum) or datum <= 0:
        return "kein_datum"
    if not _ganz(dauer_ms) or dauer_ms < 0 or dauer_ms > UHR_DAUER_MAX_MS:
        return "dauer_ungueltig"
    if not uhr_quelle_ok(quelle):
        return "quelle_ungueltig"
    if not isinstance(ordner, str) or ordner == "" or not os.path.isdir(ordner):
        return "kein_ordner"
    jetzt = jetzt if _ganz(jetzt) else int(time.time())
    ab = uhr_abweichung(datum, dauer_ms, jetzt)
    alt = uhr_lesen(ordner)
    if (isinstance(alt, dict) and alt["quelle"] == quelle and abs(alt["abweichung_s"] - ab) < UHR_SCHREIB_DELTA_S
            and jetzt - alt["zeit"] < UHR_SCHREIB_ALTER_S and jetzt >= alt["zeit"]):
        return "unveraendert"
    f = uhr_pfad(ordner)
    tmp = "%s.%d.tmp" % (f, os.getpid())
    inhalt = json.dumps({"abweichung_s": ab, "zeit": jetzt, "quelle": quelle, "fassung": UHR_FASSUNG},
                        separators=(",", ":")) + "\n"
    try:
        with open(tmp, "wb") as fh:
            fh.write(inhalt.encode("utf-8"))
        try:
            os.chmod(tmp, rechte)
        except OSError:
            pass
        os.replace(tmp, f)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return "schreibfehler"
    return "geschrieben"


def _host(url):
    try:
        from urllib.parse import urlsplit
        h = urlsplit(str(url)).hostname
        return h or ""
    except Exception:  # noqa: BLE001
        return ""


def uhr_aus_requests(ordner, antwort, quelle=None, jetzt=None):
    """Eine requests-Antwort auswerten (Kopf Date, Dauer aus antwort.elapsed). Wirft nie."""
    try:
        datum = uhr_aus_koepfen(getattr(antwort, "headers", None))
        el = getattr(antwort, "elapsed", None)
        dauer = int(el.total_seconds() * 1000) if el is not None else 0
        q = quelle if quelle is not None else _host(getattr(antwort, "url", ""))
        return uhr_merken(ordner, datum, dauer, q, jetzt=jetzt)
    except Exception:  # noqa: BLE001 - ein Abgleich darf den Abruf nie stoeren
        return "schreibfehler"


def uhr_requests_haken(ordner, quelle=None):
    """Haken fuer sitzung.hooks["response"]: wertet aus, gibt None zurueck (Antwort bleibt unveraendert)."""
    def haken(antwort, *args, **kwargs):
        uhr_aus_requests(ordner, antwort, quelle)
        return None
    return haken


def _host_passt(host, endungen):
    host = (host or "").lower()
    return any(host == e or host.endswith("." + e) for e in endungen)


def uhr_requests_ueberall(ordner, endungen):
    """requests.Session.send einmal je Prozess umhuellen, damit auch Sitzungen einer Bibliothek
    gemessen werden. Nur Antworten von Hosts mit einer der Endungen zaehlen. Rueckgabe True,
    wenn die Huelle sitzt (auch schon vorher), False ohne requests oder ohne gueltige Endung."""
    endungen = tuple(e.lower() for e in endungen if isinstance(e, str) and uhr_quelle_ok(e))
    if not endungen:
        return False
    try:
        import requests
    except ImportError:
        return False
    alt = requests.Session.send
    if getattr(alt, "_uhrabgleich", False):
        return True

    def send(self, request, **kwargs):
        antwort = alt(self, request, **kwargs)
        try:
            if _host_passt(_host(getattr(antwort, "url", "")), endungen):
                uhr_aus_requests(ordner, antwort)
        except Exception:  # noqa: BLE001
            pass
        return antwort
    send._uhrabgleich = True
    requests.Session.send = send
    return True


def uhr_aiohttp_spur(ordner, quelle=None):
    """aiohttp.TraceConfig fuer ClientSession(trace_configs=[...]); None ohne aiohttp."""
    try:
        import aiohttp
    except ImportError:
        return None
    spur = aiohttp.TraceConfig()

    async def anfang(sitzung, ctx, params):
        ctx.uhr_t0 = time.monotonic()

    async def ende(sitzung, ctx, params):
        try:
            t0 = getattr(ctx, "uhr_t0", None)
            dauer = int((time.monotonic() - t0) * 1000) if t0 is not None else 0
            datum = uhr_aus_koepfen(params.response.headers)
            q = quelle if quelle is not None else (params.url.host or "")
            uhr_merken(ordner, datum, dauer, q)
        except Exception:  # noqa: BLE001
            pass
    spur.on_request_start.append(anfang)
    spur.on_request_end.append(ende)
    return spur


def uhr_selbsttest():
    """(Faelle, Fehlschlaege, Zeilen); schreibt nur in einen eigenen Zeitordner und raeumt ihn ab."""
    import tempfile
    import shutil
    import datetime
    n = [0, 0]
    zeilen = []

    def pruef(name, ist, soll):
        n[0] += 1
        if ist != soll or type(ist) is not type(soll):
            n[1] += 1
            zeilen.append("FEHL %s: ist %r, soll %r" % (name, ist, soll))

    t0 = calendar.timegm((2026, 11, 19, 10, 0, 0, 0, 0, 0))
    pruef("datum gut", uhr_datum_lesen("Thu, 19 Nov 2026 10:00:00 GMT"), t0)
    pruef("datum Leerraum aussen", uhr_datum_lesen("  Thu, 19 Nov 2026 10:00:00 GMT \r\n"), t0)
    pruef("datum falscher Wochentag", uhr_datum_lesen("Wed, 19 Nov 2026 10:00:00 GMT"), 0)
    pruef("datum UTC statt GMT", uhr_datum_lesen("Thu, 19 Nov 2026 10:00:00 UTC"), 0)
    pruef("datum RFC 850", uhr_datum_lesen("Thursday, 19-Nov-26 10:00:00 GMT"), 0)
    pruef("datum 31. Februar", uhr_datum_lesen("Tue, 31 Feb 2026 10:00:00 GMT"), 0)
    pruef("datum Stunde 24", uhr_datum_lesen("Thu, 19 Nov 2026 24:00:00 GMT"), 0)
    pruef("datum Sekunde 60", uhr_datum_lesen("Thu, 19 Nov 2026 10:00:60 GMT"), 0)
    pruef("datum Rest dahinter", uhr_datum_lesen("Thu, 19 Nov 2026 10:00:00 GMT x"), 0)
    pruef("datum vor 2000", uhr_datum_lesen("Thu, 01 Jan 1970 00:00:00 GMT"), 0)
    pruef("datum leer", uhr_datum_lesen(""), 0)
    pruef("datum kein Text", uhr_datum_lesen(["x"]), 0)
    pruef("datum Schaltjahr", uhr_datum_lesen("Thu, 29 Feb 2024 12:34:56 GMT"), calendar.timegm((2024, 2, 29, 12, 34, 56, 0, 0, 0)))
    pruef("kopfzeile", uhr_kopfzeile("Date: Thu, 19 Nov 2026 10:00:00 GMT\r\n"), t0)
    pruef("kopfzeile klein ohne Leerraum", uhr_kopfzeile("date:Thu, 19 Nov 2026 10:00:00 GMT"), t0)
    pruef("kopfzeile fremder Name", uhr_kopfzeile("X-Date: Thu, 19 Nov 2026 10:00:00 GMT"), 0)
    pruef("koepfe Liste letzte Antwort", uhr_aus_koepfen(["HTTP/1.1 301 Moved", "Date: Thu, 19 Nov 2026 09:00:00 GMT",
                                                         "HTTP/1.1 200 OK", "Date: Thu, 19 Nov 2026 10:00:00 GMT"]), t0)
    pruef("koepfe Liste letzte ohne Date", uhr_aus_koepfen(["HTTP/1.1 301 Moved", "Date: Thu, 19 Nov 2026 09:00:00 GMT",
                                                           "HTTP/1.1 200 OK"]), 0)
    pruef("koepfe Feld", uhr_aus_koepfen({"date": "Thu, 19 Nov 2026 10:00:00 GMT", "server": "x"}), t0)
    pruef("koepfe Feld Grossschreibung", uhr_aus_koepfen({"Date": "Thu, 19 Nov 2026 10:00:00 GMT"}), t0)
    pruef("koepfe leer", uhr_aus_koepfen({}), 0)
    pruef("koepfe None", uhr_aus_koepfen(None), 0)
    pruef("koepfe fremdes Objekt", uhr_aus_koepfen(object()), 0)
    pruef("abweichung vor", uhr_abweichung(t0, 2000, t0 + 41), 40)
    pruef("abweichung nach", uhr_abweichung(t0, 0, t0 - 7), -7)
    pruef("abweichung Rundung wie PHP (1000 ms)", uhr_abweichung(t0, 1000, t0), -1)

    tmp = tempfile.mkdtemp(prefix="uhrabgleich_st_")
    try:
        pruef("lesen ohne Ablage", uhr_lesen(tmp), None)
        pruef("merken ohne Datum", uhr_merken(tmp, 0, 10, "api.test"), "kein_datum")
        pruef("merken Datum als Text", uhr_merken(tmp, str(t0), 10, "api.test"), "kein_datum")
        pruef("merken Datum als Wahrheitswert", uhr_merken(tmp, True, 10, "api.test"), "kein_datum")
        pruef("merken Dauer negativ", uhr_merken(tmp, t0, -1, "api.test"), "dauer_ungueltig")
        pruef("merken Dauer zu lang", uhr_merken(tmp, t0, UHR_DAUER_MAX_MS + 1, "api.test"), "dauer_ungueltig")
        pruef("merken Quelle leer", uhr_merken(tmp, t0, 10, ""), "quelle_ungueltig")
        pruef("merken Quelle Leerzeichen", uhr_merken(tmp, t0, 10, "a b"), "quelle_ungueltig")
        pruef("merken Quelle Zeilenumbruch", uhr_merken(tmp, t0, 10, "a\nb"), "quelle_ungueltig")
        pruef("merken ohne Ordner", uhr_merken(os.path.join(tmp, "fehlt"), t0, 10, "api.test"), "kein_ordner")
        pruef("merken legt keinen Ordner an", os.path.isdir(os.path.join(tmp, "fehlt")), False)
        pruef("merken nichts geschrieben", os.path.isfile(uhr_pfad(tmp)), False)

        def m(ab, jetzt, q="api.test"):
            return uhr_merken(tmp, jetzt - ab, 0, q, jetzt=jetzt)
        pruef("merken erstes Mal", m(5, t0 + 5), "geschrieben")
        pruef("lesen Wert", uhr_lesen(tmp), {"abweichung_s": 5, "zeit": t0 + 5, "quelle": "api.test"})
        with open(uhr_pfad(tmp), "rb") as fh:
            roh = fh.read()
        pruef("ablage Form wie PHP", roh, ('{"abweichung_s":5,"zeit":%d,"quelle":"api.test","fassung":"%s"}\n' % (t0 + 5, UHR_FASSUNG)).encode())
        pruef("ablage keine Zwischendatei", [x for x in os.listdir(tmp) if x.endswith(".tmp")], [])
        if os.name != "nt":
            pruef("ablage Rechte 0644", oct(os.stat(uhr_pfad(tmp)).st_mode & 0o777), oct(0o644))
        pruef("merken Mitte der Anfrage", uhr_merken(tmp, t0, 4000, "api.drei", jetzt=t0 + 12), "geschrieben")
        pruef("lesen Mitte der Anfrage", uhr_lesen(tmp)["abweichung_s"], 10)
        pruef("merken wieder erste Quelle", m(5, t0 + 15), "geschrieben")
        pruef("merken 1 s anders, frisch", m(6, t0 + 75), "unveraendert")
        pruef("merken 1 s anders, Wert bleibt", uhr_lesen(tmp)["abweichung_s"], 5)
        pruef("merken 2 s anders", m(7, t0 + 80), "geschrieben")
        pruef("lesen nach 2 s", uhr_lesen(tmp)["abweichung_s"], 7)
        pruef("merken -2 s anders", m(5, t0 + 81), "geschrieben")
        pruef("merken gleich, 599 s alt", m(5, t0 + 81 + 599), "unveraendert")
        pruef("merken gleich, 600 s alt", m(5, t0 + 81 + 600), "geschrieben")
        pruef("lesen Zeit nach 600 s", uhr_lesen(tmp)["zeit"], t0 + 681)
        pruef("merken andere Quelle", m(5, t0 + 682, "api.zwei"), "geschrieben")
        pruef("lesen andere Quelle", uhr_lesen(tmp)["quelle"], "api.zwei")
        pruef("merken Uhr zurueckgesprungen", m(5, t0 + 100, "api.zwei"), "geschrieben")
        for name, inhalt in (("kaputt", b"{kaputt"), ("Abweichung als Text", b'{"abweichung_s":"5","zeit":%d}' % t0),
                             ("ohne Zeit", b'{"abweichung_s":5}'), ("Zeit 0", b'{"abweichung_s":5,"zeit":0}'),
                             ("Wahrheitswert", b'{"abweichung_s":true,"zeit":%d}' % t0)):
            with open(uhr_pfad(tmp), "wb") as fh:
                fh.write(inhalt)
            pruef("lesen " + name, uhr_lesen(tmp), False)
        pruef("merken ueber kaputte Ablage", m(0, t0), "geschrieben")
        with open(uhr_pfad(tmp), "wb") as fh:
            fh.write(b'{"abweichung_s":1,"zeit":%d,"quelle":"alexa.amazon.de"}' % t0)
        pruef("lesen Ablage aus Alexa-NG 1.0.20", uhr_lesen(tmp), {"abweichung_s": 1, "zeit": t0, "quelle": "alexa.amazon.de"})

        class Antwort(object):
            def __init__(self, kopf, url, ms):
                self.headers = kopf
                self.url = url
                self.elapsed = datetime.timedelta(milliseconds=ms)
        os.unlink(uhr_pfad(tmp))
        a = Antwort({"Date": "Thu, 19 Nov 2026 10:00:00 GMT"}, "https://api.beispiel.test/x?y=1", 2000)
        pruef("requests Antwort", uhr_aus_requests(tmp, a, jetzt=t0 + 4), "geschrieben")
        pruef("requests Antwort Wert", uhr_lesen(tmp), {"abweichung_s": 3, "zeit": t0 + 4, "quelle": "api.beispiel.test"})
        pruef("requests ohne Date", uhr_aus_requests(tmp, Antwort({}, "https://api.beispiel.test/", 5)), "kein_datum")
        pruef("requests kaputtes Objekt", uhr_aus_requests(tmp, object()), "kein_datum")
        h = uhr_requests_haken(tmp, "fest.test")
        pruef("haken gibt None", h(Antwort({"Date": "Thu, 19 Nov 2026 10:00:00 GMT"}, "https://x.test/", 0)), None)
        pruef("haken feste Quelle", uhr_lesen(tmp)["quelle"], "fest.test")
        pruef("host passt Endung", _host_passt("identity.vwgroup.io", ("vwgroup.io",)), True)
        pruef("host passt gleich", _host_passt("vwgroup.io", ("vwgroup.io",)), True)
        pruef("host passt nicht (Teilwort)", _host_passt("evilvwgroup.io", ("vwgroup.io",)), False)
        pruef("ueberall ohne Endung", uhr_requests_ueberall(tmp, ("a b",)), False)
        try:
            import requests  # noqa: F401
            hat_requests = True
        except ImportError:
            hat_requests = False
        if hat_requests:
            import requests
            vorher = requests.Session.send
            try:
                pruef("ueberall sitzt", uhr_requests_ueberall(tmp, ("beispiel.test",)), True)
                erste = requests.Session.send
                pruef("ueberall einmal", uhr_requests_ueberall(tmp, ("beispiel.test",)), True)
                pruef("ueberall huellt nicht doppelt", requests.Session.send is erste, True)
                pruef("ueberall Huelle markiert", getattr(requests.Session.send, "_uhrabgleich", False), True)
            finally:
                requests.Session.send = vorher
        else:
            zeilen.append("nicht geprueft: requests fehlt (uhr_requests_ueberall)")
        spur = uhr_aiohttp_spur(tmp)
        if spur is None:
            zeilen.append("nicht geprueft: aiohttp fehlt (uhr_aiohttp_spur)")
        else:
            pruef("aiohttp Spur mit Anfang und Ende", (len(spur.on_request_start), len(spur.on_request_end)), (1, 1))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    pruef("zeitordner abgeraeumt", os.path.isdir(tmp), False)
    return n[0], n[1], zeilen


if __name__ == "__main__" and "--selbsttest" in sys.argv[1:]:
    _n, _f, _z = uhr_selbsttest()
    for _x in _z:
        print(_x)
    print("Uhrabgleich (Python) %s: %d Faelle geprueft, %d Fehlschlaege." % (UHR_FASSUNG, _n, _f))
    sys.exit(0 if _f == 0 else 1)
