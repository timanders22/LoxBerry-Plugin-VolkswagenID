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
# Zweite Schreibweise desselben Skripts fuer den Vergleich weiter unten: wurde
# der Dienst ueber einen anderen Weg auf dieselbe Datei gestartet (Symlink im
# Pfad, LBHOMEDIR gegen den aufgeloesten Ablageort), steht in seiner
# Befehlszeile eine andere Zeichenkette fuer dieselbe Datei.
SKRIPT_R=$(readlink -f "$SKRIPT" 2>/dev/null)
[ -n "$SKRIPT_R" ] || SKRIPT_R="$SKRIPT"
# Der Dienst laeuft als loxberry; wo es den Benutzer nicht gibt, als der
# eigene. Die Suche ueber /proc sieht nur dessen Prozesse an.
DIENST_UID=$(id -u loxberry 2>/dev/null || id -u)

# ---------- Laeuft gerade eine Aktualisierung dieses Plugins? ----------
#
# preupgrade.sh legt data/plugins/<ordner>.upgrade_laeuft als Erstes an,
# postupgrade.sh raeumt die Marke weg, uninstall ebenfalls. Sie liegt NEBEN
# dem Datenordner, weil purge_installation den Ordner selbst loescht.
#
# Warum: postinstall.sh legt die Zugangsdaten frueh zurueck und laedt danach
# minutenlang die Bibliothek mit pip. Der Knopf "Dienst starten" startete den
# Dienst in dieser Zeit gegen die halb eingerichtete Umgebung - am 18.09.2026
# in WSL gemessen (Pruefung-VolkswagenID-0.9.23/Pruefstaende/messe_luecke.sh,
# Fall pip_fenster: ein Dienst, erwartet keiner).
#
# Aelter als 3600 s, aus der Zukunft oder unlesbar: die Marke gilt NICHT -
# eine abgebrochene Installation darf den Dienst nicht fuer immer
# stilllegen. OHNE LESBARE UHR faellt die Pruefung GESCHLOSSEN aus: wer die
# Zeit nicht messen kann, kann das Alter nicht beurteilen und startet
# deshalb nicht (Fall marke, M10h).
#
# VW_START_TROTZ_MARKE=1 ist die Ausnahme fuer postinstall.sh: dort SOLL der
# Dienst wieder anlaufen, obwohl die Marke noch liegt - postupgrade.sh
# raeumt sie erst danach weg.
MARKE="$LBHOMEDIR/data/plugins/$PNAME.upgrade_laeuft"

upgrade_laeuft() {
    [ -f "$MARKE" ] || return 1
    [ -n "${VW_START_TROTZ_MARKE:-}" ] && return 1
    vw_dann=$(cat "$MARKE" 2>/dev/null)
    case "$vw_dann" in ''|*[!0-9]*) return 1 ;; esac
    # Aufbau wortgleich mit Chromecast4lox 1.3.11 (daemon/daemon) - dort ist
    # er gemessen, und das Werkzeug der Bestandsaufnahme erkennt genau diese
    # Form wieder.
    vw_jetzt=$(date +%s 2>/dev/null)
    case "$vw_jetzt" in ''|*[!0-9]*) vw_jetzt="" ;; esac
    if [ -z "$vw_jetzt" ]; then
        return 0
    fi
    # Bis 300 s "aus der Zukunft" gilt die Marke noch: die Uhr kann ein
    # Stueck zurueckspringen, nachdem preupgrade.sh sie gesetzt hat. In WSL
    # gemessen (Pruefung-VolkswagenID-0.9.23/messprotokoll_uhr.txt): die
    # Wanduhr sprang rund alle 30 s um 0,6 s zurueck. Mit der strengen Regel
    # "jede Sekunde Zukunft gilt nicht" fiel die Marke deshalb in zwei von 26
    # Eichlaeufen fuer einen Augenblick aus - einmal nahm die Oberflaeche das
    # Formular an, und das Passwort war weg. Weiter voraus: sie gilt nicht.
    # Dieselbe Grenze steht in vw_upgrade_lage() (webfrontend/html/vw_lib.php).
    [ "$vw_dann" -gt $((vw_jetzt + 300)) ] && return 1
    [ $((vw_jetzt - vw_dann)) -lt 3600 ]
}

mkdir -p "$PDATA" "$PLOG" 2>/dev/null

# ---------- Die eigenen Prozesse erkennen ----------
#
# Nummernrecycling ausschliessen: der Prozess muss unser Skript sein.
#
# Bis 0.9.0 ein grep ueber die ganze Befehlszeile. Die enthaelt alle
# Argumente; hat die wiederverwendete Nummer einen Editor mit geoeffneter
# vw.py erwischt, galt der als laufender Dienst. Verglichen wird seit 0.9.1
# argumentweise gegen den vollen Pfad.
#
# Ein Treffer hat GENAU zwei Argumente: argv[0] ist ein Python, argv[1] ist
# genau unser Skript. Die zweite Bedingung braucht es, weil "nano /pfad/vw.py"
# ebenfalls den vollen Pfad als zweites Argument fuehrt. Das DRITTE Argument
# schliesst die Einmallaeufe aus (--selbsttest, --einmal, --mqtt-leeren;
# bin/vw.py:2888-2896): sie laufen als eigener Prozess, sind aber nicht der
# Dauerlaeufer. Bis 0.9.22 fehlte diese Bedingung. In WSL gemessen
# (Pruefung-VolkswagenID-0.9.22, FALL 6): ein "python3 <pfad>/vw.py
# --selbsttest", dessen Nummer in der PID-Datei stand, galt als Dienst -
# "status" meldete "laeuft <pid>", und nach "stop" war er tot.
# Der Dauerlaeufer wird an genau einer Stelle gestartet, in starten(), als
# "$PY" "$SKRIPT".
#
# Gelesen wird ohne Hilfsprogramm: "read -d ''" zerlegt die Befehlszeile am
# Nullbyte. Das spart je Prozess einen Aufruf von tr - der Waechter laeuft
# minuetlich.
ist_dienst() {
    [ -r "/proc/$1/cmdline" ] || return 1
    {
        IFS= read -r -d '' vw_a0 || return 1
        IFS= read -r -d '' vw_a1 || return 1
        case "${vw_a0##*/}" in python|python3|python3.*) ;; *) return 1 ;; esac
        if [ "$vw_a1" != "$SKRIPT" ]; then
            case "$vw_a1" in
                /*) vw_voll="$vw_a1" ;;
                *)  vw_cwd=$(readlink -f "/proc/$1/cwd" 2>/dev/null)
                    [ -n "$vw_cwd" ] || return 1
                    vw_voll="$vw_cwd/$vw_a1" ;;
            esac
            [ "$(readlink -f "$vw_voll" 2>/dev/null)" = "$SKRIPT_R" ] || return 1
        fi
        IFS= read -r -d '' vw_a2 && return 1
        return 0
    } < "/proc/$1/cmdline"
}

# Alle eigenen Dienste, aufsteigend und ohne Dubletten.
#
# Zwei Quellen, weil keine allein reicht:
#   - die Suche ueber /proc findet auch einen Dienst OHNE PID-Datei.
#     purge_installation raeumt data/plugins/<ordner>/ bei jedem Upgrade ab
#     (Regeln/06); der Minutentakt kann in der Luecke einen zweiten starten.
#     In WSL gemessen (FALL 8): "stop" meldete "angehalten", und danach lief
#     noch ein eigener Dienst.
#   - die PID-Datei findet auch einen Dienst, der einem anderen Benutzer
#     gehoert (von Hand als root gestartet) und deshalb durch den
#     Benutzerfilter faellt.
dienste() {
    {
        for vw_d in /proc/[0-9]*; do
            ist_dienst "${vw_d#/proc/}" || continue
            [ "$(stat -c %u "$vw_d" 2>/dev/null)" = "$DIENST_UID" ] || continue
            echo "${vw_d#/proc/}"
        done
        vw_p=""
        [ -f "$PID" ] && IFS= read -r vw_p < "$PID" 2>/dev/null
        case "$vw_p" in
            ''|*[!0-9]*) ;;
            *) ist_dienst "$vw_p" && echo "$vw_p" ;;
        esac
    } | sort -un
}

laeuft() {
    [ -n "$(dienste)" ]
}

starten() {
    # Die Marke steht VOR allem anderen - auch vor dem touch des Sollmerkers
    # weiter unten: solange sie gilt, wird nichts gestartet und nichts
    # angelegt. Kein Fehler (Rueckgabewert 0) - der Minutentakt soll sich
    # nicht beschweren, und postinstall.sh startet gleich selbst. Die Pruefung
    # sitzt hier und nicht im case-Verteiler, damit sie fuer 'start',
    # 'restart' UND den Zweig 'waechter' gilt - alle drei fuehren hierher.
    if upgrade_laeuft; then
        echo "Eine Aktualisierung dieses Plugins laeuft - es wird nichts gestartet."
        return 0
    fi
    LAUFEND=$(dienste)
    if [ -n "$LAUFEND" ]; then
        ERSTE=$(printf '%s\n' "$LAUFEND" | head -n 1)
        # Die PID-Datei nachziehen, wenn sie fehlt oder veraltet ist. Die
        # Nummer ist argumentweise geprueft - eine ungepruefte Nummer darf
        # hier nie hinein.
        echo "$ERSTE" > "$PID" 2>/dev/null
        echo "laeuft bereits (PID $ERSTE)"
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
    # ALLE eigenen Dienste, nicht nur den aus der PID-Datei.
    ZIEL=$(dienste)
    if [ -z "$ZIEL" ]; then
        rm -f "$PID"
        echo "laeuft nicht"
        return 0
    fi
    kill $ZIEL 2>/dev/null
    for i in 1 2 3 4 5 6 7 8 9 10; do
        [ -n "$(dienste)" ] || break
        sleep 1
    done
    # Vor dem harten Signal wird NEU gesucht, nicht die Liste von vorhin
    # wiederverwendet: zwischen den beiden Signalen kann ein Prozess enden und
    # seine Nummer neu vergeben werden. Bis 0.9.22 ging das kill -9 an die
    # Nummer, die zehn Sekunden zuvor aus der PID-Datei gelesen worden war.
    REST=$(dienste)
    if [ -n "$REST" ]; then
        kill -9 $REST 2>/dev/null
        sleep 1
    fi
    rm -f "$PID"
    # "angehalten" ist eine Zusicherung, kein Rueckgabewert: es wird
    # nachgesehen (CLAUDE.md, "Wirkung pruefen, nicht Rueckgabewert").
    UEBRIG=$(dienste)
    if [ -n "$UEBRIG" ]; then
        echo "FEHLER: Dienst laeuft weiter (PID $(printf '%s' "$UEBRIG" | tr '\n' ' '))"
        return 1
    fi
    echo "angehalten"
    return 0
}

case "$1" in
    start)   starten ;;
    stop)    anhalten ;;
    restart) anhalten; sleep 1; starten ;;
    status)
        LAUFEND=$(dienste)
        if [ -n "$LAUFEND" ]; then
            echo "laeuft $(printf '%s' "$LAUFEND" | tr '\n' ' ' | sed 's/ $//')"
            exit 0
        fi
        echo "gestoppt"
        exit 1
        ;;
    waechter)
        # Nur neu starten, wenn der Dienst laufen SOLL. Ein bewusst
        # angehaltener Dienst bleibt angehalten.
        # Die Marke wird HIER schon gefragt und nicht erst in starten():
        # sonst kappte der Waechter die Startdatei nicht, schriebe aber jede
        # Minute "Dienst lief nicht, wird neu gestartet" ins Protokoll,
        # obwohl gleich darauf nichts gestartet wird (Fall marke, M10j).
        if [ -f "$SOLL" ] && ! laeuft && ! upgrade_laeuft; then
            echo "[$(date '+%Y-%m-%d %H:%M:%S')] Waechter: Dienst lief nicht, wird neu gestartet." >> "$LOGDATEI"
            starten >> "$STARTLOG" 2>&1
        fi
        ;;
    *)
        echo "Aufruf: $0 {start|stop|restart|status|waechter}"
        exit 2
        ;;
esac
