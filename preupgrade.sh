#!/bin/bash
# Volkswagen ID - preupgrade
# command <TEMPFOLDER> <NAME> <FOLDER> <VERSION> <BASEFOLDER>
#
# Vor dem Upgrade: laufenden Dienst anhalten und die Konfiguration ausserhalb
# des Plugin-Ordners sichern. Die Zugangsdaten liegen in einer eigenen Datei
# und werden getrennt gesichert (Rechte 0600 bleiben erhalten).
ARGV3=$3
ARGV5=$5
PFOLDER="${ARGV3:-volkswagenid}"
BASE="${ARGV5:-$LBHOMEDIR}"

# ---------- Die Wurzel: GELESEN, nicht geraten ----------
#
# Bis 0.9.23 stand hier NUR die Zeile darueber - ohne Rueckfall und ohne
# Pruefung. Bleibt $5 leer und ist $LBHOMEDIR nicht gesetzt, dann ist $BASE
# leer, und die naechste Zeile lautete "mkdir -p /data/plugins": ein
# absoluter Pfad ausserhalb jedes LoxBerry, und dieses Skript laeuft am
# Geraet als root. In WSL gemessen (Pruefung-VolkswagenID-0.9.24, Fall K7).
#
# Gesucht wird aufwaerts nach config/plugins, data/plugins UND
# config/system/general.json (Regeln/06); ohne Wurzel wird gewarnt statt
# vollzogen - ohne Marke laeuft die Aktualisierung weiter, nur mit dem alten
# Risiko.
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
    echo "<WARNING> Es wurde kein LoxBerry-Wurzelverzeichnis gefunden: weder als"
    echo "<WARNING> fuenftes Argument noch in \$LBHOMEDIR, und oberhalb von $SELF"
    echo "<WARNING> traegt kein Verzeichnis config/plugins, data/plugins und"
    echo "<WARNING> config/system/general.json. Es wurde nichts angelegt, nichts"
    echo "<WARNING> gesichert und kein Dienst angehalten."
    exit 1
fi

# ---------- Die Upgrade-Marke, als ERSTES ----------
#
# Zwischen dem Kopieren der neuen Dateien und postinstall.sh liegt fast eine
# Minute (Regeln/06, am Geraet gemessen), und postinstall.sh selbst steht
# danach Minuten im pip-Lauf. In dieser Zeit sind config/plugins/<ordner>/
# und data/plugins/<ordner>/ geloescht oder erst halb zurueckgelegt, die
# Oberflaeche ist aber erreichbar.
#
# Fuer DIESE Linie am 18.09.2026 in WSL gemessen
# (Pruefung-VolkswagenID-0.9.23/Pruefstaende/messe_luecke.sh):
#   ui_post      Oberflaeche in der Luecke, Formular "Einstellungen"
#                unveraendert abgesendet: zugang.json entstand als
#                {"email":"","passwort":"","spin":""}. postinstall.sh spielt
#                die Zweitschrift nur zurueck, wenn die Datei leer oder "{}"
#                ist - Konto und Passwort waren nach der Aktualisierung weg.
#   ui_post_alt  dasselbe mit einer Seite, die VOR dem Upgrade geoeffnet
#                war: das Passwort war weg.
#   pip_fenster  der Knopf "Dienst starten" (direkt und ueber die
#                Oberflaeche) startete den Dienst, waehrend postinstall.sh
#                noch die Bibliothek lud.
# Der Minutentakt allein startet in der Luecke nichts (soll_laufen liegt im
# geloeschten Datenordner) - die Einstufung "Vorsorge" stimmte nur fuer ihn.
#
# Die Marke liegt NEBEN dem Datenordner - purge_installation loescht
# data/plugins/<ordner>/, den Nachbarn mit dem Punkt trifft es nicht. Sie
# traegt die Unixzeit; bin/dienst.sh und die Oberflaeche achten sie, solange
# sie juenger als 3600 s ist. postupgrade.sh raeumt sie weg (das letzte
# Hakenskript dieser Linie), uninstall ebenfalls, postinstall.sh bei einem
# Abbruch.
MARKE="$BASE/data/plugins/$PFOLDER.upgrade_laeuft"
mkdir -p "$BASE/data/plugins" 2>/dev/null
if date +%s > "$MARKE" 2>/dev/null && grep -qE '^[0-9]+$' "$MARKE" 2>/dev/null; then
    echo "<OK> Aktualisierung angemeldet - Dienststart und Oberflaeche halten still."
else
    # Die Wirkung pruefen, nicht den Rueckgabewert (Kernschicht 2). Ohne
    # Marke laeuft die Aktualisierung weiter, nur mit dem alten Risiko.
    rm -f "$MARKE" 2>/dev/null
    echo "<WARNING> Die Marke $MARKE liess sich nicht anlegen; waehrend der"
    echo "<WARNING> Aktualisierung bitte die Plugin-Oberflaeche nicht benutzen."
fi

# Der Merker sagt dem postinstall, dass der Dienst LIEF.
#
# BERICHTIGT am 03.09.2026, und diesmal an der Primaerquelle gemessen.
#
# Hier stand seit dem 20.08.2026, purge_installation laufe "ausschliesslich
# im Deinstallations-Zweig (:233)" und der Sollmerker ueberlebe das Upgrade.
# Das war falsch. Nachgemessen an sbin/plugininstall.pl (Zweig master, 2054
# Zeilen, selbst geholt am 03.09.2026):
#
#   233:	&purge_installation("all");   <- Deinstallation
#   886:		&purge_installation;      <- IM UPGRADE-ZWEIG
#
# Zeile 886 steht innerhalb von "if ($isupgrade) {" (:858), unmittelbar nach
# den preupgrade-Skripten, unter dem Kommentar "# Purge old installation".
# Im Rumpf der Subroutine, unter "if ($pfolder)" und OHNE jede Pruefung auf
# "all", steht rm -rf auf config/plugins/<ordner>/ UND
# data/plugins/<ordner>/. Das "all" an :233 schaltet nur zusaetzlich die
# Crontab-Datei und das uninstall-Skript frei.
#
# Was daraus folgt: der Sollmerker data/plugins/<ordner>/soll_laufen
# UEBERLEBT DAS UPGRADE NICHT. Der Cron-Waechter holt den Dienst also
# NICHT von selbst zurueck - dieser Merker hier ist der einzige Weg, und
# er behebt sehr wohl einen Stillstand. Er liegt deshalb neben dem
# Konfigordner (Dateiname mit Punkt), wo der Loeschblock ihn nicht
# erwischt.
MERKER="$BASE/config/plugins/$PFOLDER.lief_vorher"
# Ein Merker aus einem FRUEHEREN Update bleibt liegen (I2, Durchgang
# 02.10.2026): er faellt erst, wenn postinstall.sh den Dienst wirklich
# gestartet hat. Bis 0.9.26 wurde er hier geloescht - nach einem Update ohne
# Netz (Dienst tot, Merker liegen geblieben) startete das naechste Update
# mit Netz den Dienst deshalb nicht mehr (gemessen, Installerbericht O1b).
if [ -f "$MERKER" ]; then
    echo "<INFO> Ein frueheres Update hat den Dienst nicht wieder gestartet - er wird"
    echo "<INFO> nach diesem Update gestartet."
fi

PIDDATEI="$BASE/data/plugins/$PFOLDER/dienst.pid"

# ---------- Die eigenen Prozesse erkennen ----------
#
# Argumentweise (Regeln/03, "Prozesse argumentweise erkennen"), gleichlautend
# mit uninstall/uninstall und bin/dienst.sh dieser Fassung.
#
# Bis 0.9.22 stand hier die Nummer aus der PID-Datei ganz ohne Pruefung vor
# BEIDEN Signalen: "kill $(cat PID)", zwei Sekunden warten, dann bedingungslos
# "kill -9 $(cat PID)". Das kill -0 darueber setzte nur den Merker. In WSL
# gemessen (Pruefung-VolkswagenID-0.9.22, FALL 1): ein Koeder "sleep 600",
# dessen Nummer in der PID-Datei stand, war nach preupgrade.sh tot -
# "<INFO> Laufender Dienst angehalten." bei einem Vorgang, der mit diesem
# Plugin nichts zu tun hat. Prozessnummern werden wiederverwendet; eine
# liegengebliebene PID-Datei zeigt nach einem Neustart auf irgendetwas.
#
# Ein Treffer hat GENAU zwei Argumente: argv[0] ist ein Python, argv[1] ist
# genau der eigene Dienstpfad. Das dritte Argument schliesst die Einmallaeufe
# aus (--selbsttest, --einmal, --mqtt-leeren; bin/vw.py:2888-2896) - sie sind
# eigene Prozesse, aber nicht der Dauerlaeufer und duerfen hier nicht
# mitgehen. Gemessen (FALL 4): bis 0.9.22 starb ein --selbsttest, dessen
# Nummer in der PID-Datei stand.
#
# Wurde der Dienst mit einem relativen Pfad gestartet, wird er ueber das
# Arbeitsverzeichnis des PROZESSES aufgeloest, nicht ueber das eigene -
# readlink -f auf "vw.py" bezoege sich sonst auf den falschen Ordner. Ist
# /proc/<pid>/cwd nicht lesbar, faellt die Pruefung geschlossen aus: dann
# gilt der Prozess als fremd und wird nicht angefasst.
VW_SKRIPT="$BASE/bin/plugins/$PFOLDER/vw.py"
VW_SKRIPT_R=$(readlink -f "$VW_SKRIPT" 2>/dev/null)
[ -n "$VW_SKRIPT_R" ] || VW_SKRIPT_R="$VW_SKRIPT"
# Der Dienst laeuft als loxberry; wo es den Benutzer nicht gibt, als der
# eigene. Die Suche ueber /proc sieht nur dessen Prozesse an.
VW_UID=$(id -u loxberry 2>/dev/null || id -u)

ist_unser_dienst() {
    [ -r "/proc/$1/cmdline" ] || return 1
    {
        IFS= read -r -d '' vw_a0 || return 1
        IFS= read -r -d '' vw_a1 || return 1
        case "${vw_a0##*/}" in python|python3|python3.*) ;; *) return 1 ;; esac
        if [ "$vw_a1" != "$VW_SKRIPT" ]; then
            case "$vw_a1" in
                /*) vw_voll="$vw_a1" ;;
                *)  vw_cwd=$(readlink -f "/proc/$1/cwd" 2>/dev/null)
                    [ -n "$vw_cwd" ] || return 1
                    vw_voll="$vw_cwd/$vw_a1" ;;
            esac
            [ "$(readlink -f "$vw_voll" 2>/dev/null)" = "$VW_SKRIPT_R" ] || return 1
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
#     In WSL gemessen (FALL 3): ohne diesen Schritt lief ein Dienst ohne
#     PID-Datei durch das ganze Upgrade weiter.
#   - die PID-Datei findet auch einen Dienst, der einem anderen Benutzer
#     gehoert (von Hand als root gestartet) und deshalb durch den
#     Benutzerfilter faellt.
vw_dienste() {
    {
        for vw_d in /proc/[0-9]*; do
            ist_unser_dienst "${vw_d#/proc/}" || continue
            [ "$(stat -c %u "$vw_d" 2>/dev/null)" = "$VW_UID" ] || continue
            echo "${vw_d#/proc/}"
        done
        vw_p=""
        [ -f "$PIDDATEI" ] && IFS= read -r vw_p < "$PIDDATEI" 2>/dev/null
        case "$vw_p" in
            ''|*[!0-9]*) ;;
            *) ist_unser_dienst "$vw_p" && echo "$vw_p" ;;
        esac
    } | sort -un
}

# Beendet sie: freundlich, bis zu zehn Sekunden Zeit, dann hart - und vor
# JEDEM Signal wird neu gesucht, auch vor dem kill -9. Zwischen den beiden
# Signalen kann ein Prozess enden und seine Nummer neu vergeben werden; die
# Liste von vorhin wird deshalb nicht wiederverwendet. Zehn Sekunden statt
# zwei: der Dienst haelt eine offene HTTP-Sitzung zur Volkswagen-Cloud und
# schreibt seine Dateien ueber os.replace().
# Gibt die Nummern aus, die beim ersten Signal gemeint waren.
vw_dienste_beenden() {
    vw_ziel=$(vw_dienste)
    [ -n "$vw_ziel" ] || return 0
    kill $vw_ziel 2>/dev/null
    vw_i=0
    while [ $vw_i -lt 10 ] && [ -n "$(vw_dienste)" ]; do
        sleep 1
        vw_i=$((vw_i + 1))
    done
    vw_rest=$(vw_dienste)
    [ -n "$vw_rest" ] && kill -9 $vw_rest 2>/dev/null
    echo $vw_ziel
}

# Eine Nummer, die NICHT dem Abrufdienst gehoert, wird GENANNT, nicht beendet.
# Sonst sieht der Betreiber im Installationsprotokoll "Laufender Dienst
# angehalten." und weiss nicht, dass etwas anderes erschlagen wurde. "Nicht
# der Abrufdienst" heisst hier beides: ein voellig fremder Vorgang, der die
# wiederverwendete Nummer bekommen hat, oder ein Einmallauf dieses Plugins.
if [ -f "$PIDDATEI" ]; then
    VW_P=$(cat "$PIDDATEI" 2>/dev/null)
    if [ -n "$VW_P" ] && kill -0 "$VW_P" 2>/dev/null && ! ist_unser_dienst "$VW_P"; then
        echo "<INFO> Die Nummer $VW_P aus der PID-Datei gehoert nicht dem"
        echo "<INFO> Abrufdienst - es wurde nichts beendet, die Datei wird entfernt."
    fi
fi

# Der Merker sagt dem postinstall, dass der Dienst LIEF - und er entsteht nur,
# wenn wirklich einer beendet wurde. Eine liegengebliebene PID-Datei ist kein
# laufender Dienst und darf nach dem Upgrade keinen Start ausloesen, den
# niemand gewollt hat.
VW_BEENDET=$(vw_dienste_beenden)
if [ -n "$VW_BEENDET" ]; then
    : > "$MERKER"
    echo "<INFO> Der Dienst lief - er wird nach dem Upgrade wieder gestartet."
    echo "<INFO> Laufender Dienst angehalten (PID $VW_BEENDET)."
else
    echo "<INFO> Der Dienst lief nicht - es war nichts anzuhalten."
fi
rm -f "$PIDDATEI"

# Die Zuordnung Fahrgestellnummer -> Fahrzeugnummer liegt bereits NEBEN dem
# Datenordner und ueberlebt damit den Loeschblock. Hier wird sie nur
# gesichert, falls eine aeltere Fassung sie noch DARIN abgelegt hat.
ALTNR="$BASE/data/plugins/$PFOLDER/nummern.json"
NEUNR="$BASE/data/plugins/$PFOLDER.nummern.json"
if [ -f "$ALTNR" ] && [ ! -f "$NEUNR" ]; then
    cp -p "$ALTNR" "$NEUNR" 2>/dev/null || true
    echo "<INFO> Fahrzeugnummern gesichert."
fi

# ---------- Rettung des Datenordners (I3, Durchgang 02.10.2026) ----------
#
# purge_installation loescht data/plugins/<ordner>/ bei jedem Upgrade. Bis
# 0.9.26 waren danach Anmeldemarken (token.json), Verlauf, Ladeprotokoll und
# die Fortschreibung eines laufenden Ladevorgangs fort (gemessen,
# Installerbericht U1). Sie gehen NEBEN den Ordner: unter .neu gebaut, mit tar
# (Zeitstempel bleiben), gezaehlt, erst dann umbenannt (Regeln/06). Eine
# Rettung aus einem frueheren, abgebrochenen Lauf wird nicht wiederverwendet,
# sondern nach .alt gelegt (Entscheidung 1).
PDATA_ALT="$BASE/data/plugins/$PFOLDER"
RETTUNG="$BASE/data/plugins/$PFOLDER.rettung"
if [ -e "$RETTUNG" ] || [ -L "$RETTUNG" ]; then
    if [ -L "$RETTUNG.alt" ] || [ -f "$RETTUNG.alt" ]; then
        rm -f "$RETTUNG.alt"
    elif [ -d "$RETTUNG.alt" ]; then
        rm -rf "${RETTUNG:?}.alt"
    fi
    if mv -f "$RETTUNG" "$RETTUNG.alt" 2>/dev/null; then
        echo "<WARNING> Eine Rettung aus einem frueheren, abgebrochenen Update wurde nicht wiederverwendet, sondern beiseitegelegt: $RETTUNG.alt"
    else
        echo "<WARNING> Eine Rettung aus einem frueheren Update liess sich nicht beiseitelegen: $RETTUNG"
    fi
fi
VW_LISTE=""
for f in token.json verlauf ladungen.csv fortschreibung.json; do
    [ -e "$PDATA_ALT/$f" ] && [ ! -L "$PDATA_ALT/$f" ] && VW_LISTE="$VW_LISTE $f"
done
if [ -n "$VW_LISTE" ] && [ ! -e "$RETTUNG" ]; then
    rm -rf "${RETTUNG:?}.neu"
    if mkdir -p "$RETTUNG.neu" && chmod 700 "$RETTUNG.neu" \
       && (cd "$PDATA_ALT" && tar cf - $VW_LISTE) | (cd "$RETTUNG.neu" && tar xpf -); then
        VW_FEHLT=""
        for f in $VW_LISTE; do
            [ -e "$RETTUNG.neu/$f" ] || VW_FEHLT="$VW_FEHLT $f"
        done
        if [ -z "$VW_FEHLT" ] && mv -f "$RETTUNG.neu" "$RETTUNG"; then
            [ -f "$RETTUNG/token.json" ] && chmod 600 "$RETTUNG/token.json"
            echo "<OK> Gerettet:$VW_LISTE (Verlauf: $(ls "$RETTUNG/verlauf" 2>/dev/null | wc -l) Dateien)."
        else
            rm -rf "${RETTUNG:?}.neu"
            echo "<WARNING> Die Rettung ist unvollstaendig (fehlt:$VW_FEHLT) - nichts gerettet."
        fi
    else
        rm -rf "${RETTUNG:?}.neu"
        echo "<WARNING> Verlauf, Ladeprotokoll und Anmeldemarken liessen sich nicht retten."
    fi
fi

# ---------- Die virtuelle Umgebung (I2, Durchgang 02.10.2026) ----------
#
# purge_installation loescht bin/plugins/<ordner>/ samt venv. Sie geht neben
# den Ordner (bin/plugins/<ordner>.venv); postinstall.sh legt sie an denselben
# Pfad zurueck und holt nur dann etwas aus dem Netz, wenn die festgenagelten
# Fassungen nicht passen. Bis 0.9.26 war ein Update ohne Internet das Ende des
# Dienstes (gemessen, Installerbericht O1). Eine alte Ablage wird vorher
# entfernt (Entscheidung 1).
VENV_JETZT="$BASE/bin/plugins/$PFOLDER/venv"
VENV_ABLAGE="$BASE/bin/plugins/$PFOLDER.venv"
if [ -L "$VENV_ABLAGE" ]; then
    rm -f "$VENV_ABLAGE"
elif [ -d "$VENV_ABLAGE" ]; then
    rm -rf "${VENV_ABLAGE:?}"
fi
if [ -d "$VENV_JETZT" ] && [ ! -L "$VENV_JETZT" ]; then
    if mv -f "$VENV_JETZT" "$VENV_ABLAGE"; then
        echo "<OK> Virtuelle Umgebung beiseitegelegt: $VENV_ABLAGE"
    else
        echo "<INFO> Die virtuelle Umgebung liess sich nicht beiseitelegen - sie wird neu angelegt."
    fi
fi

CFGDIR="$BASE/config/plugins/$PFOLDER"
for f in vw.json zugang.json; do
    if [ -f "$CFGDIR/$f" ]; then
        cp -p "$CFGDIR/$f" "$BASE/config/plugins/$PFOLDER.backup.$f" || true
    fi
done
# BEIDE Zweitschriften auf 0600. cp -p uebernimmt zwar den Quellmodus, aber
# in vw.json steht das Aktionstoken des unangemeldeten Endpunkts, und aus
# einer aelteren Fassung kann die Datei noch mit 0644 dastehen.
chmod 600 "$BASE/config/plugins/$PFOLDER.backup.zugang.json" 2>/dev/null || true
chmod 600 "$BASE/config/plugins/$PFOLDER.backup.vw.json" 2>/dev/null || true
echo "<OK> preupgrade abgeschlossen."
exit 0
