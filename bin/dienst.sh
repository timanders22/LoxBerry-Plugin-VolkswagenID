#!/bin/bash
# Volkswagen ID - Start, Stopp und Waechter des Abrufdienstes.
#
# Die Pfade werden aus dem EIGENEN Ablageort abgeleitet, nicht ueber
# LoxBerry::System. Grund: LoxBerry::System leitet den Pluginordner aus dem
# Aufrufort ab; wird dieses Skript aus postinstall.sh oder aus dem Cron
# gestartet, kommt dort ueberall Leerstring zurueck - das Skript werkelt dann
# gegen /-Pfade und meldet trotzdem Erfolg.

# readlink -f loest Symlinks auf, BEVOR das Verzeichnis bestimmt wird.
# LoxBerry legt Daemons als Symlink unter system/daemons/plugins/ ab; von
# dort aufgerufen ergaebe dirname "$0" den Pfad .../system/daemons/plugins,
# der Pluginname waere buchstaeblich "plugins", und PID-Datei, Sollmerker
# und Logdatei landeten neben dem eigenen Ordner statt darin. Die
# Oberflaeche saehe den Dienst dann nie laufen, und der Waechter startete
# ihn im Minutentakt ein zweites Mal.
# Als loxberry laufen, nicht als root.
#
# Der minuetliche Waechter kommt aus dem Cron. Laeuft der als root - und je
# nach Ablage des Cronjobs tut er das -, dann gehoerten PID-Datei, Sollmerker
# und Protokoll danach root. Die Oberflaeche laeuft als loxberry und koennte
# den Dienst anschliessend weder anhalten noch neu starten: sie darf die
# Dateien nicht mehr schreiben. Schlimmer noch, 'dienst.sh stop' meldet dann
# Erfolg - das kill scheitert, aber das rm der PID-Datei gelingt, weil das
# Verzeichnis loxberry gehoert. Der Dienst laeuft weiter und ist nur noch
# ueber die Prozessliste zu finden.
#
# Deshalb setzt sich das Skript selbst herunter, EINMAL und bevor es
# irgendetwas anlegt. exec, damit kein zusaetzlicher Prozess stehen bleibt.
# '-s /bin/bash' ausdruecklich: ohne das nimmt su die Login-Shell aus
# /etc/passwd. Steht dort nologin oder /bin/false, endet dieses Skript hier
# still und ohne Meldung - und weil es 'exec' ist, kaeme nicht einmal ein
# Rueckgabewert zurueck. Auf einem regulaeren LoxBerry ist der Zweig ohnehin
# unerreichbar (der Cron laeuft bereits als loxberry); er greift nur, wenn
# jemand von Hand mit sudo aufruft.
#
# Woertlich uebernommen aus LoxBerry-Plugin-Dashboard-0.9.12, dort seit dem
# 16.08.2026 in Betrieb. Ueber den Bestand gezaehlt am 31.08.2026: 15 von 17
# dienst.sh hatten den Abstieg nicht, obwohl REGELN_2 ihn seit langem
# verlangt.
if [ "$(id -u)" = "0" ] && id loxberry >/dev/null 2>&1; then
    exec su -s /bin/bash loxberry -c "$(printf '%q ' "$0" "$@")"
fi

SELF=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)          # <home>/bin/plugins/<ordner>
PNAME=$(basename "$SELF")
LBHOMEDIR=$(cd "$SELF/../../.." && pwd)
PDATA="$LBHOMEDIR/data/plugins/$PNAME"
PLOG="$LBHOMEDIR/log/plugins/$PNAME"
PCONFIG="$LBHOMEDIR/config/plugins/$PNAME"
PID="$PDATA/dienst.pid"
SOLL="$PDATA/soll_laufen"
LOGDATEI="$PLOG/vw.log"
# Eigene Datei fuer alles, was NEBEN dem Protokoll anfaellt: Meldungen des
# Starts und alles, was das Programm nach stderr schreibt, bevor sein
# Protokoll steht (Syntaxfehler, fehlende Bibliothek, Abbruch im Importpfad).
#
# Bis 0.9.15 ging diese Ausgabe mit ">> $LOGDATEI" in DIESELBE Datei, die
# bin/vw.py mit einem umlaufenden Handler fuehrt. Das haelt einen zweiten,
# anhaengenden Deskriptor auf diese Datei offen. Beim Ueberlauf benennt der
# Handler um, beim Leeren der Ramdisk verschwindet die Datei ganz - der
# Deskriptor dieser Shell zeigt danach weiter auf die weggeschobene oder
# geloeschte Datei, und was er traegt, sieht niemand mehr. Am Geraet gemessen
# (06.09.2026): sieben Dienste hielten so eine geloeschte Protokolldatei offen.
# Regel: genau einer schreibt in eine Protokolldatei.
STARTLOG="$PLOG/vw_start.log"
PY="$SELF/venv/bin/python3"
SKRIPT="$SELF/vw.py"

mkdir -p "$PDATA" "$PLOG" 2>/dev/null

laeuft() {
    [ -f "$PID" ] || return 1
    P=$(cat "$PID" 2>/dev/null)
    [ -n "$P" ] || return 1
    kill -0 "$P" 2>/dev/null || return 1
    # Nummernrecycling ausschliessen: der Prozess muss unser Skript sein.
    #
    # Bis 0.9.0 ein grep ueber die ganze Befehlszeile. Die enthaelt alle
    # Argumente; hat die wiederverwendete Nummer einen Editor mit geoeffneter
    # vw.py erwischt, galt der als laufender Dienst. Verglichen wird jetzt
    # argumentweise gegen den vollen Pfad - cmdline trennt die Argumente mit
    # Nullbytes, die tr in Zeilen verwandelt.
    # Zwei Bedingungen: das zweite Argument ist genau unser Skript, und das
    # erste ist ein Python. Die zweite braucht es, weil "nano /pfad/vw.py"
    # ebenfalls den vollen Pfad als zweites Argument fuehrt - nachgestellt
    # und bestaetigt. Der Dienst laeuft immer als
    # "<venv>/bin/python3 <pfad>/vw.py".
    ARGS=$(tr '\0' '\n' < "/proc/$P/cmdline" 2>/dev/null)
    [ "$(echo "$ARGS" | sed -n '2p')" = "$SKRIPT" ] || return 1
    echo "$ARGS" | sed -n '1p' | grep -qE '(^|/)python[0-9.]*$' || return 1
    return 0
}

starten() {
    if laeuft; then
        echo "laeuft bereits (PID $(cat "$PID"))"
        return 0
    fi
    if [ ! -x "$PY" ]; then
        echo "FEHLER: virtuelle Python-Umgebung fehlt ($PY). Plugin neu installieren."
        return 1
    fi
    # Auf INHALT pruefen, nicht nur auf Vorhandensein.
    #
    # Bis 0.9.9 stand hier nur -f. Eine Datei mit leerem Passwort - und die
    # entsteht beim ersten Speichern der E-Mail-Adresse - liess den Start zu;
    # vw.py stellte dann fest, dass die Zugangsdaten fehlen, gab 1 zurueck und
    # war weg. Der Sollmerker blieb liegen, der Waechter startete jede Minute
    # neu: rund 1440 Zyklen am Tag, jeder mit drei Zeilen im Protokoll.
    if [ ! -f "$PCONFIG/zugang.json" ]; then
        echo "FEHLER: Zugangsdaten fehlen ($PCONFIG/zugang.json). Erst in der Oberflaeche eintragen."
        return 1
    fi
    if ! grep -q '"passwort"[[:space:]]*:[[:space:]]*"[^"]\{1,\}"' "$PCONFIG/zugang.json" 2>/dev/null \
       || ! grep -q '"email"[[:space:]]*:[[:space:]]*"[^"]\{1,\}"' "$PCONFIG/zugang.json" 2>/dev/null; then
        echo "FEHLER: In $PCONFIG/zugang.json fehlt der Benutzername oder das Passwort."
        echo "        Reiter Einstellungen der Plugin-Oberflaeche. Der Dienst wird nicht"
        echo "        gestartet - sonst liefe er in eine Neustartschleife."
        return 1
    fi
    # Die Ausgabe des Dienstes geht in die Startdatei, NICHT in das Protokoll:
    # dort schreibt allein der Handler des Programms. Beim Start gekappt, damit
    # sie nur die Ausgabe EINES Laufes sammelt und nicht unbegrenzt waechst.
    : > "$STARTLOG"
    nohup "$PY" "$SKRIPT" >> "$STARTLOG" 2>&1 &
    echo $! > "$PID"
    sleep 1
    if laeuft; then
        # Der Sollmerker entsteht ERST, wenn der Dienst wirklich laeuft
        # (seit 0.9.12). Bis 0.9.11 stand das touch davor. Scheiterte der
        # Start aus einem anderen Grund als den beiden oben geprueften -
        # eine unvollstaendige venv, ein Importfehler, ein nicht
        # beschreibbares Protokoll -, blieb der Merker liegen, und der
        # minuetliche Waechter startete endlos weiter: dieselbe
        # Neustartschleife, die 0.9.10 fuer den Fall der leeren
        # Zugangsdaten geschlossen hat.
        touch "$SOLL"
        echo "gestartet (PID $(cat "$PID"))"
        return 0
    fi
    echo "FEHLER: Start fehlgeschlagen - siehe $STARTLOG und $LOGDATEI"
    rm -f "$PID"
    return 1
}

anhalten() {
    rm -f "$SOLL"
    if ! laeuft; then
        rm -f "$PID"
        echo "laeuft nicht"
        return 0
    fi
    P=$(cat "$PID")
    kill "$P" 2>/dev/null
    for i in 1 2 3 4 5 6 7 8 9 10; do
        laeuft || break
        sleep 1
    done
    if laeuft; then
        kill -9 "$P" 2>/dev/null
        sleep 1
    fi
    rm -f "$PID"
    echo "angehalten"
    return 0
}

case "$1" in
    start)   starten ;;
    stop)    anhalten ;;
    restart) anhalten; sleep 1; starten ;;
    status)
        if laeuft; then
            echo "laeuft $(cat "$PID")"
            exit 0
        fi
        echo "gestoppt"
        exit 1
        ;;
    waechter)
        # Nur neu starten, wenn der Dienst laufen SOLL. Ein bewusst
        # angehaltener Dienst bleibt angehalten.
        if [ -f "$SOLL" ] && ! laeuft; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Waechter: Dienst lief nicht, wird neu gestartet." >> "$LOGDATEI"
            starten >> "$STARTLOG" 2>&1
        fi
        ;;
    *)
        echo "Aufruf: $0 {start|stop|restart|status|waechter}"
        exit 2
        ;;
esac
