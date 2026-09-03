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
if [ -z "$BASE" ] || [ ! -d "$BASE" ]; then
    # Ableitung aus dem eigenen Ablageort - LoxBerry::System taugt hier nicht,
    # weil es den Pluginordner aus dem Aufrufort ableitet und aus
    # postinstall.sh heraus ueberall Leerstring liefert.
    SELF=$(cd "$(dirname "$0")" && pwd)
    BASE=$(cd "$SELF/../.." 2>/dev/null && pwd)
fi

PBIN="$BASE/bin/plugins/$PFOLDER"
PDATA="$BASE/data/plugins/$PFOLDER"
PLOG="$BASE/log/plugins/$PFOLDER"
PCONFIG="$BASE/config/plugins/$PFOLDER"
VENV="$PBIN/venv"

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

# Sicherung zurueckspielen (uebersteht Update UND Neuinstallation)
for f in vw.json zugang.json; do
    BK="$BASE/config/plugins/$PFOLDER.backup.$f"
    CF="$PCONFIG/$f"
    if [ -f "$BK" ]; then
        INHALT=$(cat "$CF" 2>/dev/null)
        if [ ! -s "$CF" ] || [ "$INHALT" = "{}" ]; then
            cp -p "$BK" "$CF" && echo "<OK> $f aus Sicherung wiederhergestellt."
        fi
    fi
done
chmod 600 "$PCONFIG/zugang.json"

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
BRAUCHBAR=0
if [ -x "$VENV/bin/python3" ]; then
    if "$VENV/bin/python3" -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)' 2>/dev/null; then
        BRAUCHBAR=1
    fi
fi
if [ "$BRAUCHBAR" -eq 0 ]; then
    rm -rf "$VENV"
    if ! "$PY" -m venv "$VENV"; then
        echo "<FAIL> Virtuelle Umgebung konnte nicht angelegt werden ($VENV)."
        echo "<FAIL> Fehlt das Paket python3-venv? (apt install python3-venv)"
        exit 1
    fi
    echo "<OK> Virtuelle Umgebung angelegt: $VENV"
fi
if [ ! -x "$VENV/bin/python3" ]; then
    echo "<FAIL> $VENV/bin/python3 fehlt - Abbruch."
    exit 1
fi

"$VENV/bin/python3" -m pip install --upgrade pip setuptools wheel >/dev/null 2>&1 || \
    echo "<INFO> pip liess sich nicht aktualisieren - wird mit der vorhandenen Fassung versucht."

echo "<INFO> Installiere carconnectivity $KERN und den Volkswagen-Connector $CONNECTOR"
echo "<INFO> (benoetigt eine Internetverbindung) ..."
if ! "$VENV/bin/python3" -m pip install --no-cache-dir \
        "carconnectivity==$KERN" "carconnectivity-connector-volkswagen==$CONNECTOR"; then
    echo "<INFO> Feste Fassungen nicht installierbar - versuche die neuesten."
    if ! "$VENV/bin/python3" -m pip install --no-cache-dir \
            "carconnectivity-connector-volkswagen"; then
        echo "<FAIL> carconnectivity konnte nicht installiert werden."
        echo "<FAIL> Haeufigste Ursachen: keine Internetverbindung, oder PyPI war"
        echo "<FAIL> nicht erreichbar."
        exit 1
    fi
    # Ersatzweg gegangen - und angezeigt, sonst wird aus dem Ersatz unbemerkt
    # der Normalfall. Bei einer anderen Fassung koennen sich Feldnamen
    # geaendert haben.
    echo "<INFO> ERSATZWEG: Es wurden die neuesten Fassungen statt $KERN / $CONNECTOR"
    echo "<INFO> installiert. Falls Werte leer bleiben, im Reiter Test"
    echo "<INFO> 'Rohdaten als JSON ansehen' aufrufen und vergleichen."
fi

# ---------- paho-mqtt: freiwillig, und ein Fehlschlag ist keiner ----------
#
# Gebraucht wird es NUR fuer die Ladeempfehlung und die Vorklimatisierung -
# beide sind ab Werk aus. Ohne paho laeuft alles uebrige unveraendert; der
# Selbsttest sagt dann, was fehlt. Deshalb bricht ein Fehlschlag hier die
# Installation NICHT ab: ein Plugin, das wegen einer freiwilligen Zutat gar
# nicht erst startet, ist schlechter als eines mit einer Funktion weniger.
echo "<INFO> Installiere paho-mqtt (nur fuer Ladeempfehlung und Vorklimatisierung) ..."
if "$VENV/bin/python3" -m pip install --no-cache-dir "paho-mqtt" >/dev/null 2>&1 \
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
if ! "$VENV/bin/python3" -c 'from carconnectivity.carconnectivity import CarConnectivity' 2>/dev/null; then
    echo "<FAIL> carconnectivity ist installiert, laesst sich aber nicht laden."
    exit 1
fi
if ! "$VENV/bin/python3" -c 'import carconnectivity_connectors.volkswagen.connector' 2>/dev/null; then
    echo "<FAIL> Der Volkswagen-Connector ist installiert, laesst sich aber nicht laden."
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
MERKER="$BASE/config/plugins/$PFOLDER.lief_vorher"
if [ -f "$MERKER" ]; then
    rm -f "$MERKER"
    if [ ! -x "$PBIN/dienst.sh" ]; then
        echo "<INFO> $PBIN/dienst.sh fehlt - der Dienst wurde nicht gestartet."
    else
        # Als loxberry und nicht als root: der Dienst schreibt in data/
        # und log/. Was root dort anlegt, kann die Oberflaeche danach
        # nicht mehr ueberschreiben.
        if [ "$(id -u)" = "0" ]; then
            AUSGABE=$(su -s /bin/bash -c "$PBIN/dienst.sh start" loxberry 2>&1)
        else
            AUSGABE=$("$PBIN/dienst.sh" start 2>&1)
        fi
        case "$AUSGABE" in
            *gestartet*|*laeuft*)
                echo "<OK> Dienst wieder gestartet: $AUSGABE" ;;
            *)
                echo "<INFO> Der Dienst liess sich nicht wieder starten: $AUSGABE"
                echo "<INFO> Reiter Einstellungen, Knopf 'Dienst starten'." ;;
        esac
    fi
fi

echo "<OK> Installation abgeschlossen."
echo "<INFO> Bitte die Plugin-Oberflaeche oeffnen, die Zugangsdaten des Volkswagen-Kontos"
echo "<INFO> eintragen und den Dienst im Reiter Einstellungen starten."
echo "<INFO> Hinweis: Der Connector arbeitet mit europaeischen Fahrzeugen. Fuer"
echo "<INFO> Nordamerika gibt es einen eigenen, hier nicht eingebauten Connector."
exit 0
