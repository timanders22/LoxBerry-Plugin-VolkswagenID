#!/bin/bash
# Volkswagen ID - postinstall
# command <TEMPFOLDER> <NAME> <FOLDER> <VERSION> <BASEFOLDER>
#
# Legt an: Konfigurations-, Daten- und Logordner, die Zugangsdatei mit Rechten
# 0600 und die virtuelle Python-Umgebung samt der Bibliothek carconnectivity
# und ihrem Volkswagen-Connector.
#
# WICHTIG (PEP 668): Debian 12/13 kennzeichnen die System-Python-Umgebung als
# extern verwaltet. Ein systemweites "pip3 install" wird mit
# "error: externally-managed-environment" abgewiesen - auch mit --user, auch
# als root. Deshalb eine eigene venv, und der Shebang der Skripte zeigt direkt
# darauf. JEDER Rueckgabewert wird geprueft: eine Installation, die "ALLES
# ERLEDIGT" meldet, obwohl die venv fehlschlug, ist schlimmer als ein Abbruch.
#
# Python: carconnectivity verlangt 3.9 oder neuer. Das erfuellt jeder LoxBerry,
# den es heute gibt (Debian 12 liefert 3.11, Debian 13 liefert 3.13). Die
# Pruefung bleibt trotzdem stehen - lieber eine benannte Meldung als ein
# stillschweigend totes Plugin.

ARGV3=$3
ARGV5=$5
PFOLDER="${ARGV3:-volkswagenid}"
BASE="${ARGV5:-$LBHOMEDIR}"
# Ableitung aus dem eigenen Ablageort - LoxBerry::System taugt hier nicht,
# weil es den Pluginordner aus dem Aufrufort ableitet und aus postinstall.sh
# heraus ueberall Leerstring liefert.
#
# Bis 0.9.23 waren es zwei feste Ebenen ($SELF/../..). Dieses Skript liegt
# beim Einbau im Auspackordner - zwei Ebenen darueber ist irgendein
# Verzeichnis, nicht die Wurzel. Gemessen (Pruefung-VolkswagenID-0.9.24,
# Fall K6): in einem fremden Baum legte postinstall.sh dort Ordner an.
# Deshalb aufwaerts suchen, und zwar nach config/plugins, data/plugins UND
# config/system/general.json (Regeln/06). Ohne Wurzel wird abgebrochen -
# alles, was danach kommt, schreibt unter $BASE.
vw_wurzel_suchen() {
    vw_v=$(cd "$1" 2>/dev/null && pwd -P) || return 1
    vw_i=0
    while [ -n "$vw_v" ] && [ "$vw_v" != "/" ] && [ "$vw_i" -lt 8 ]; do
        if [ -d "$vw_v/config/plugins" ] && [ -d "$vw_v/data/plugins" ] \
           && [ -f "$vw_v/config/system/general.json" ]; then
            echo "$vw_v"
            return 0
        fi
        vw_v=$(dirname "$vw_v")
        vw_i=$((vw_i + 1))
    done
    return 1
}
SELF=$(cd "$(dirname "$0")" && pwd)
if [ -z "$BASE" ] || [ ! -d "$BASE/config/plugins" ] || [ ! -d "$BASE/data/plugins" ]; then
    BASE=$(vw_wurzel_suchen "$SELF") || BASE=""
fi
if [ -z "$BASE" ]; then
    echo "<FAIL> Es wurde kein LoxBerry-Wurzelverzeichnis gefunden: weder als"
    echo "<FAIL> fuenftes Argument noch in \$LBHOMEDIR, und oberhalb von $SELF"
    echo "<FAIL> traegt kein Verzeichnis config/plugins, data/plugins und"
    echo "<FAIL> config/system/general.json. Es wurde nichts angelegt."
    exit 1
fi

PBIN="$BASE/bin/plugins/$PFOLDER"
PDATA="$BASE/data/plugins/$PFOLDER"
PLOG="$BASE/log/plugins/$PFOLDER"
PCONFIG="$BASE/config/plugins/$PFOLDER"
VENV="$PBIN/venv"

# Die Upgrade-Marke aus preupgrade.sh. Sie faellt regulaer in
# postupgrade.sh, NACH dem Dienststart unten (Begruendung dort und in
# Pruefung-VolkswagenID-0.9.23/README.md). Steigt dieses Skript aber mit
# einem Fehler aus (Ordner, Python, venv, pip, Ladeversuch), faellt sie
# hier: sonst bliebe die Plugin-Oberflaeche nach einer gescheiterten
# Installation eine Stunde lang gesperrt, ohne dass irgendwo stuende, warum.
# Eine Kommandoersetzung und eine Unterschale loesen den EXIT-Trap nicht aus
# (Regeln/06); der Rueckgabewert des Skripts bleibt unveraendert.
MARKE="$BASE/data/plugins/$PFOLDER.upgrade_laeuft"
trap 'vw_rc=$?; [ "$vw_rc" -ne 0 ] && rm -f "$MARKE" 2>/dev/null' EXIT

# ---------- Upgrade oder Neuinstallation? (I1, Durchgang 02.10.2026) ----------
#
# Entscheidung 1 (29.09.2026): zurueckgespielt wird NUR bei einer
# Aktualisierung, und eine Aktualisierung erkennt dieses Skript an der Marke,
# die preupgrade.sh als Erstes anlegt - ohne Altersvergleich (Entscheidung 8).
# Bis 0.9.26 spielte auch eine Neuinstallation die Zweitschriften einer
# FRUEHEREN Installation ein - altes Konto, Passwort, S-PIN, Aktionstoken -,
# startete den Dienst und meldete "Aktualisierung abgeschlossen" (gemessen,
# Installerbericht N1).
UPG_MARKE=0
[ -f "$MARKE" ] && UPG_MARKE=1
RETTUNG="$BASE/data/plugins/$PFOLDER.rettung"
VENV_GERETTET="$PBIN.venv"

# Neuinstallation: was preinstall.sh nicht schon beiseitegelegt hat (etwa
# weil es fehlte), geht hier nach .alt - einmal <WARNING> mit den Pfaden.
if [ "$UPG_MARKE" = 0 ]; then
    ALT_LISTE=""
    for z in "$BASE/config/plugins/$PFOLDER.backup.vw.json" \
             "$BASE/config/plugins/$PFOLDER.backup.zugang.json" \
             "$BASE/config/plugins/$PFOLDER.lief_vorher" \
             "$BASE/data/plugins/$PFOLDER.nummern.json" "$RETTUNG"; do
        [ -e "$z" ] || [ -L "$z" ] || continue
        if [ -L "$z.alt" ] || [ -f "$z.alt" ]; then
            rm -f "$z.alt"
        elif [ -d "$z.alt" ]; then
            rm -rf "${z:?}.alt"
        fi
        if mv -f "$z" "$z.alt" 2>/dev/null; then
            ALT_LISTE="$ALT_LISTE $z.alt"
        else
            ALT_LISTE="$ALT_LISTE $z (liess sich NICHT verschieben)"
        fi
    done
    if [ -L "$VENV_GERETTET" ]; then
        rm -f "$VENV_GERETTET"
    elif [ -d "$VENV_GERETTET" ]; then
        rm -rf "${VENV_GERETTET:?}"
    fi
    if [ -n "$ALT_LISTE" ]; then
        echo "<WARNING> Neuinstallation: Einstellungen, Zugangsdaten und Bestaende einer frueheren Installation wurden nicht eingespielt, sondern beiseitegelegt:$ALT_LISTE - der Dienst wird nicht gestartet; die Deinstallation raeumt sie ab."
    fi
fi

# Fassungen, gegen die dieses Plugin gebaut wurde. Auf einen Stand
# festgenagelt, damit eine Installation von heute morgen und eine von heute
# abend dasselbe ergeben. Die Feldnamen im Dienst stammen aus genau diesen
# Fassungen; eine neuere kann andere haben.
KERN="0.11.10"
CONNECTOR="0.10.6"

mkdir -p "$PDATA" "$PLOG" "$PCONFIG" "$PDATA/befehle" "$PDATA/antworten" || {
    echo "<FAIL> Ordner konnten nicht angelegt werden."
    exit 1
}
chmod 755 "$PDATA" "$PLOG" "$PCONFIG" 2>/dev/null

# ---------- Konfiguration ----------
[ -f "$PCONFIG/vw.json" ] || echo '{}' > "$PCONFIG/vw.json"
if [ ! -f "$PCONFIG/zugang.json" ]; then
    echo '{}' > "$PCONFIG/zugang.json"
fi
chmod 600 "$PCONFIG/zugang.json"

# Sicherung zurueckspielen - NUR BEI EINER AKTUALISIERUNG (I1, Durchgang
# 02.10.2026). Bis 0.9.26 stand hier "uebersteht Update UND
# Neuinstallation" - genau das war der Befund. Bei einer Neuinstallation
# liegen die Zweitschriften schon unter .alt (oben, preinstall.sh).
#
# UEBERNOMMEN merkt sich, ob hier etwas aus einem frueheren Einbau
# weiterlebt. Das Schlusswort haengt NICHT mehr allein daran (siehe dort):
# zurueckgespielt wird auch eine Sicherung ohne Zugangsdaten.
UEBERNOMMEN=0
[ "$UPG_MARKE" = 1 ] && for f in vw.json zugang.json; do
    BK="$BASE/config/plugins/$PFOLDER.backup.$f"
    CF="$PCONFIG/$f"
    if [ -f "$BK" ]; then
        INHALT=$(cat "$CF" 2>/dev/null)
        if [ ! -s "$CF" ] || [ "$INHALT" = "{}" ]; then
            cp -p "$BK" "$CF" && echo "<OK> $f aus Sicherung wiederhergestellt." \
                && UEBERNOMMEN=1
        fi
    fi
done
chmod 600 "$PCONFIG/zugang.json"

# ---------- Rettung aus preupgrade.sh zurueckholen (I3, Durchgang 02.10.2026) ----------
#
# purge_installation loescht data/plugins/<ordner>/ bei jedem Upgrade. Bis
# 0.9.26 waren danach Anmeldemarken, Verlauf, Ladeprotokoll und die
# Fortschreibung eines laufenden Ladevorgangs fort (gemessen, Installerbericht
# U1), obwohl die README eine Aufbewahrung von 8 bis 90 Tagen zusagt. Nur mit
# Marke; was im neuen Datenordner schon liegt, wird nicht ueberschrieben.
if [ "$UPG_MARKE" = 1 ] && [ -d "$RETTUNG" ] && [ ! -L "$RETTUNG" ]; then
    ZURUECK=""
    for f in token.json verlauf ladungen.csv fortschreibung.json; do
        if { [ -e "$RETTUNG/$f" ] || [ -L "$RETTUNG/$f" ]; } && [ ! -e "$PDATA/$f" ]; then
            if [ -L "$RETTUNG/$f" ]; then
                echo "<WARNING> $RETTUNG/$f ist ein Verweis - nicht zurueckgeholt."
            elif mv -f "$RETTUNG/$f" "$PDATA/$f"; then
                ZURUECK="$ZURUECK $f"
            fi
        fi
    done
    [ -f "$PDATA/token.json" ] && chmod 600 "$PDATA/token.json"
    if [ -n "$ZURUECK" ]; then
        echo "<OK> Aus der Rettung zurueckgeholt:$ZURUECK (Verlauf: $(ls "$PDATA/verlauf" 2>/dev/null | wc -l) Dateien)."
    fi
    rm -rf "${RETTUNG:?}"
fi

# ---------- Python suchen ----------
PY=""
for k in python3.13 python3.12 python3.11 python3.10 python3.9; do
    if command -v "$k" >/dev/null 2>&1; then PY="$k"; break; fi
done
if [ -z "$PY" ] && command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)'; then
        PY="python3"
    fi
fi
if [ -z "$PY" ]; then
    HAVE=$(python3 -V 2>&1 || echo "kein python3")
    echo "<FAIL> Es wurde kein Python 3.9 oder neuer gefunden (gefunden: $HAVE)."
    echo "<FAIL> Die Bibliothek carconnectivity setzt Python >= 3.9 voraus."
    echo "<FAIL> Das Plugin bleibt installiert, der Dienst kann aber nicht starten."
    exit 1
fi
echo "<INFO> Verwendetes Python: $PY ($($PY -V 2>&1))"

# ---------- virtuelle Umgebung ----------
#
# DIE GERETTETE UMGEBUNG ZUERST (I2, Durchgang 02.10.2026). preupgrade.sh legt
# die venv neben den Ordner (bin/plugins/<ordner>.venv), weil
# purge_installation den Ordner selbst loescht. Bis 0.9.26 wurde sie bei
# jedem Update neu aus dem Netz geholt: ein Update ohne Internet oder bei
# einer PyPI-Stoerung endete mit "<FAIL> carconnectivity konnte nicht
# installiert werden", und der Dienst blieb tot (gemessen, Installerbericht
# O1/O1b). Zurueck an denselben Pfad - die venv kennt ihren Ort.
GERETTET=0
if [ "$UPG_MARKE" = 1 ] && [ -d "$VENV_GERETTET" ] && [ ! -L "$VENV_GERETTET" ] && [ ! -e "$VENV" ]; then
    if mv -f "$VENV_GERETTET" "$VENV"; then
        GERETTET=1
        echo "<INFO> Virtuelle Umgebung der vorigen Fassung uebernommen: $VENV"
    fi
fi
if [ -L "$VENV_GERETTET" ]; then
    rm -f "$VENV_GERETTET"
elif [ -d "$VENV_GERETTET" ]; then
    rm -rf "${VENV_GERETTET:?}"
fi
BRAUCHBAR=0
if [ -x "$VENV/bin/python3" ]; then
    if "$VENV/bin/python3" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
        BRAUCHBAR=1
    fi
fi
if [ "$BRAUCHBAR" -eq 0 ]; then
    GERETTET=0
    rm -rf "$VENV"
    if ! "$PY" -m venv "$VENV"; then
        echo "<FAIL> Virtuelle Umgebung konnte nicht angelegt werden ($VENV)."
        echo "<FAIL> Das Paket python3-venv fehlt; der Installer zieht es ueber dpkg/apt"
        echo "<FAIL> selbst nach. Ist das gescheitert, bitte die Installation wiederholen."
        exit 1
    fi
    echo "<OK> Virtuelle Umgebung angelegt: $VENV"
fi
if [ ! -x "$VENV/bin/python3" ]; then
    echo "<FAIL> $VENV/bin/python3 fehlt - Abbruch."
    exit 1
fi

# Passen die festgenagelten Fassungen schon (gerettete venv)? Dann ohne pip.
PIP_NOETIG=1
if [ "$GERETTET" = 1 ]; then
    IST0=$("$VENV/bin/python3" -c 'import importlib.metadata as m; print(m.version("carconnectivity"), m.version("carconnectivity-connector-volkswagen"))' 2>/dev/null)
    if [ "$IST0" = "$KERN $CONNECTOR" ]; then
        PIP_NOETIG=0
        echo "<OK> carconnectivity $KERN und der Volkswagen-Connector $CONNECTOR sind schon"
        echo "<OK> in der uebernommenen Umgebung - es wird nichts aus dem Netz geholt."
    else
        echo "<INFO> In der uebernommenen Umgebung stehen andere Fassungen (${IST0:-unbekannt}) -"
        echo "<INFO> es wird nachinstalliert."
    fi
fi

if [ "$PIP_NOETIG" = 1 ]; then
"$VENV/bin/python3" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1 || \
    echo "<INFO> pip liess sich nicht aktualisieren - wird mit der vorhandenen Fassung versucht."

echo "<INFO> Installiere carconnectivity $KERN und den Volkswagen-Connector $CONNECTOR"
echo "<INFO> (benoetigt eine Internetverbindung) ..."
if ! "$VENV/bin/python3" -m pip install --no-cache-dir \
        "carconnectivity==$KERN" "carconnectivity-connector-volkswagen==$CONNECTOR"; then
    echo "<INFO> Feste Fassungen nicht installierbar - versuche die neuesten."
    if ! "$VENV/bin/python3" -m pip install --no-cache-dir \
            "carconnectivity-connector-volkswagen"; then
        if [ "$GERETTET" = 1 ]; then
            # I2: scheitert pip, bleibt die gerettete Umgebung in Betrieb.
            echo "<WARNING> carconnectivity liess sich nicht nachinstallieren (keine"
            echo "<WARNING> Internetverbindung oder PyPI nicht erreichbar). Es bleibt die"
            echo "<WARNING> Bibliothek der vorigen Fassung in Betrieb (${IST0:-Fassung unbekannt});"
            echo "<WARNING> das naechste Update mit Netz holt die festgenagelten Fassungen."
        else
            echo "<FAIL> carconnectivity konnte nicht installiert werden."
            echo "<FAIL> Haeufigste Ursachen: keine Internetverbindung, oder PyPI war"
            echo "<FAIL> nicht erreichbar."
            exit 1
        fi
    else
    # Ersatzweg gegangen - und angezeigt, sonst wird aus dem Ersatz unbemerkt
    # der Normalfall. Bei einer anderen Fassung koennen sich Feldnamen
    # geaendert haben.
    echo "<INFO> ERSATZWEG: Es wurden die neuesten Fassungen statt $KERN / $CONNECTOR"
    echo "<INFO> installiert. Falls Werte leer bleiben, im Reiter Test"
    echo "<INFO> 'Rohdaten als JSON ansehen' aufrufen und vergleichen."
    fi
fi
fi

# ---------- paho-mqtt: freiwillig, und ein Fehlschlag ist keiner ----------
#
# Gebraucht wird es NUR fuer die Ladeempfehlung und die Vorklimatisierung -
# beide sind ab Werk aus. Ohne paho laeuft alles uebrige unveraendert; der
# Selbsttest sagt dann, was fehlt. Deshalb bricht ein Fehlschlag hier die
# Installation NICHT ab: ein Plugin, das wegen einer freiwilligen Zutat gar
# nicht erst startet, ist schlechter als eines mit einer Funktion weniger.
if [ "$GERETTET" = 1 ] && "$VENV/bin/python3" -c 'import paho.mqtt.client' 2>/dev/null; then
    echo "<OK> paho-mqtt ist vorhanden (uebernommene Umgebung)."
elif echo "<INFO> Installiere paho-mqtt (nur fuer Ladeempfehlung und Vorklimatisierung) ..." \
   && "$VENV/bin/python3" -m pip install --no-cache-dir "paho-mqtt" >/dev/null 2>&1 \
   && "$VENV/bin/python3" -c 'import paho.mqtt.client' 2>/dev/null; then
    echo "<OK> paho-mqtt ist vorhanden."
else
    echo "<INFO> paho-mqtt liess sich nicht installieren. Das ist KEIN Fehler:"
    echo "<INFO> Abruf, Endpunkt, MQTT-Ausgabe und alle Schaltbefehle arbeiten"
    echo "<INFO> unveraendert. Es entfallen nur Ladeempfehlung und"
    echo "<INFO> Vorklimatisierung; der Reiter Test sagt es ebenfalls."
fi

# Rueckgabewert allein genuegt nicht - es wird nachgesehen, ob sich beide
# Pakete auch laden lassen.
#
# Pythons eigene Meldung geht ins Installationsprotokoll (I7, Durchgang
# 02.10.2026; Regeln/06): bis 0.9.26 stand dort nur, DASS der Import scheitert,
# nicht warum (2>/dev/null).
if ! LADEFEHLER=$("$VENV/bin/python3" -c 'from carconnectivity.carconnectivity import CarConnectivity' 2>&1); then
    echo "<FAIL> carconnectivity ist installiert, laesst sich aber nicht laden:"
    printf '%s\n' "$LADEFEHLER" | tail -n 5 | sed 's/^/<FAIL>     /'
    exit 1
fi
if ! LADEFEHLER=$("$VENV/bin/python3" -c 'import carconnectivity_connectors.volkswagen.connector' 2>&1); then
    echo "<FAIL> Der Volkswagen-Connector ist installiert, laesst sich aber nicht laden:"
    printf '%s\n' "$LADEFEHLER" | tail -n 5 | sed 's/^/<FAIL>     /'
    exit 1
fi
IST=$("$VENV/bin/python3" -c 'import importlib.metadata as m; print(m.version("carconnectivity"), m.version("carconnectivity-connector-volkswagen"))' 2>/dev/null || echo "unbekannt")
echo "<OK> carconnectivity geladen, Fassungen: $IST"

# ---------- Rechte ----------
chmod 755 "$PBIN/vw.py" 2>/dev/null
chmod 755 "$PBIN/dienst.sh" 2>/dev/null
chown -R loxberry:loxberry "$PBIN" "$PDATA" "$PLOG" "$PCONFIG" 2>/dev/null
# Rechte am Ende noch einmal festziehen.
#
# vw.json bekommt jetzt ebenfalls 0600. Darin stehen zwar keine Passwoerter,
# aber die Fahrgestellnummer, das MQTT-Thema und das Token des unangemeldeten
# Endpunkts - mit letzterem kann jeder, der es lesen kann, ueber HTTP das
# Fahrzeug schalten. Es gibt keinen Grund, warum ein anderer Systembenutzer
# die Datei lesen koennen muss; der Dienst laeuft als loxberry.
chmod 600 "$PCONFIG/vw.json" 2>/dev/null
chmod 600 "$PCONFIG/zugang.json"
chmod 600 "$PDATA/token.json" 2>/dev/null

# ---------- Dienst wieder starten, wenn er vor dem Upgrade lief ----------
#
# Der Merker entsteht nur in preupgrade.sh und nur dann, wenn dort ein
# laufender Vorgang angehalten wurde. Bei einer Erstinstallation gibt es
# ihn nicht, und dann passiert hier nichts.
#
# Er behebt sehr wohl einen Stillstand. Berichtigt am 03.09.2026: hier
# stand, der Sollmerker unter data/ ueberlebe das Upgrade und der
# Cron-Waechter hole den Dienst binnen einer Minute zurueck. An
# sbin/plugininstall.pl nachgemessen ist das falsch - purge_installation
# wird auch im Upgrade-Zweig gerufen (:886) und loescht
# data/plugins/<ordner>/ vollstaendig. Ohne diesen Merker bliebe das
# Plugin nach jedem Update still, bis jemand von Hand startet.
#
# Er wird IN JEDEM FALL entfernt, auch wenn der Start scheitert. Ein
# liegengebliebener Merker startete den Dienst bei einer spaeteren
# Installation ungefragt - auch dann, wenn er absichtlich abgeschaltet
# worden war.
#
# Seit dem Durchgang 02.10.2026 NUR MIT MARKE (I1): eine Neuinstallation
# startet keinen Dienst aus dem Merker einer frueheren Installation (er liegt
# dann ohnehin unter .alt). Und der Merker bleibt liegen, bis ein Start
# GELINGT (I2): bis 0.9.26 fiel er auch bei einem gescheiterten Start, und das
# naechste Update mit Netz liess den Dienst angehalten (gemessen O1b).
MERKER="$BASE/config/plugins/$PFOLDER.lief_vorher"
DIENST_LIEF=0
if [ "$UPG_MARKE" = 1 ] && [ -f "$MERKER" ]; then
    DIENST_LIEF=1
    if [ ! -x "$PBIN/dienst.sh" ]; then
        echo "<INFO> $PBIN/dienst.sh fehlt - der Dienst wurde nicht gestartet."
    else
        # Als loxberry und nicht als root: der Dienst schreibt in data/
        # und log/. Was root dort anlegt, kann die Oberflaeche danach
        # nicht mehr ueberschreiben.
        #
        # VW_START_TROTZ_MARKE=1: HIER soll der Dienst anlaufen, auch wenn
        # die Upgrade-Marke aus preupgrade.sh noch liegt. Sie faellt erst in
        # postupgrade.sh. Fiele sie vor diesem Start, waere fuer diesen
        # Augenblick auch der Knopf der Oberflaeche offen - und zwei
        # gleichzeitige "dienst.sh start" legen zwei Dienste an (in WSL
        # gemessen, Pruefung-VolkswagenID-0.9.23/Pruefstaende/messe_rennen.sh).
        if [ "$(id -u)" = "0" ]; then
            AUSGABE=$(su -s /bin/bash -c "VW_START_TROTZ_MARKE=1 $PBIN/dienst.sh start" loxberry 2>&1)
        else
            AUSGABE=$(VW_START_TROTZ_MARKE=1 "$PBIN/dienst.sh" start 2>&1)
        fi
        # DIE WIRKUNG ENTSCHEIDET, NICHT DER WORTLAUT (I2, Durchgang 02.10.2026). Bis
        # 0.9.26 stand hier ein Muster ueber die Ausgabe (*gestartet*|*laeuft*) -
        # und "Der Dienst wird nicht gestartet" enthaelt "gestartet": ein
        # gescheiterter Start erschien als "<OK> Dienst wieder gestartet: FEHLER ..."
        # (gemessen, Bauprobe O2). Jetzt wird dienst.sh status gefragt.
        if "$PBIN/dienst.sh" status >/dev/null 2>&1; then
            rm -f "$MERKER"
            echo "<OK> Dienst wieder gestartet: $AUSGABE"
            UEBERNOMMEN=1
        else
            echo "<WARNING> Der Dienst liess sich nicht wieder starten: $AUSGABE"
            echo "<WARNING> Reiter Einstellungen, Knopf 'Dienst starten'. Der Merker bleibt"
            echo "<WARNING> liegen; das naechste Update startet den Dienst erneut."
        fi
    fi
fi

# ---------- Schlusswort ----------
# Dieses Skript laeuft bei der Erstinstallation UND bei jedem Upgrade
# (plugininstall.pl uebergibt kein Kennzeichen). Bis 0.9.24 entschied hier
# UEBERNOMMEN - also ob OBEN etwas zurueckkopiert wurde, nicht was. Eine
# Sicherung mit leerer Adresse und leerem Passwort wurde ebenso kopiert, und
# dann stand "es ist nichts weiter zu tun" ueber einem Plugin, das sich nie
# anmelden kann (gemessen 24.09.2026, Pruefung-VolkswagenID-0.9.25/
# postinstall_hinweis.md, Fall c).
# Jetzt entscheidet der INHALT von zugang.json: E-Mail UND Passwort nicht
# leer - dieselbe Bedingung, unter der der Dienst ueberhaupt anlaeuft
# (bin/vw.py, dienst(): "if not z["email"] or not z["passwort"]"). Ist die
# Datei unlesbar oder fehlt eines davon, erscheint die Anleitung.
EINGERICHTET=0
if "$PY" -c '
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as d:
        z = json.load(d)
except Exception:
    sys.exit(1)
ok = (isinstance(z, dict) and str(z.get("email") or "").strip() != ""
      and str(z.get("passwort") or "") != "")
sys.exit(0 if ok else 1)
' "$PCONFIG/zugang.json" 2>/dev/null; then
    EINGERICHTET=1
fi

# Seit dem Durchgang 02.10.2026 entscheidet zuerst die Marke (I1): eine
# Neuinstallation heisst nie "Aktualisierung abgeschlossen".
if [ "$EINGERICHTET" -eq 1 ] && [ "$UPG_MARKE" = 1 ]; then
    echo "<OK> Aktualisierung abgeschlossen, Einstellungen uebernommen (Zugangsdaten vorhanden)."
    if [ "$DIENST_LIEF" -eq 0 ]; then
        echo "<INFO> Der Dienst lief vor dem Upgrade nicht und wurde nicht gestartet"
        echo "<INFO> (Reiter Einstellungen, Knopf 'Dienst starten')."
    fi
elif [ "$EINGERICHTET" -eq 1 ]; then
    echo "<OK> Installation abgeschlossen."
    echo "<INFO> Der Dienst ist angehalten; gestartet wird er im Reiter Einstellungen."
else
    if [ "$UEBERNOMMEN" -eq 1 ]; then
        echo "<WARNING> Die Einstellungen wurden aus der Sicherung zurueckgespielt, sie"
        echo "<WARNING> enthalten aber keine Zugangsdaten (E-Mail und Passwort)."
    fi
    echo "<OK> Installation abgeschlossen."
    echo "<INFO> Bitte die Plugin-Oberflaeche oeffnen, die Zugangsdaten des Volkswagen-Kontos"
    echo "<INFO> eintragen und den Dienst im Reiter Einstellungen starten."
    echo "<INFO> Hinweis: Der Connector arbeitet mit europaeischen Fahrzeugen. Fuer"
    echo "<INFO> Nordamerika gibt es einen eigenen, hier nicht eingebauten Connector."
fi
exit 0
