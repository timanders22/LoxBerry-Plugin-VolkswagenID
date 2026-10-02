#!/bin/bash
# Volkswagen ID - preinstall
# command <TEMPFOLDER> <NAME> <FOLDER> <VERSION> <BASEFOLDER>
#
# Seit dem Durchgang 02.10.2026 (I1, Entscheidung 1 vom 29.09.2026; Bauform
# Abfahrts-Assistent 1.6.16). Der Installer ruft dieses Skript bei JEDEM Einbau
# auf, nach dem Aufraeumen der alten Fassung und VOR dem Kopieren von Cron-Datei
# und Oberflaeche (sbin/plugininstall.pl: preupgrade :846, purge :874,
# preinstall :877, Cron :990, HTML :1066 - Geraet/2026-09-05/08_plugininstall.pl).
#
# Eine Aktualisierung erkennt es allein an der Marke
# data/plugins/<ordner>.upgrade_laeuft, die preupgrade.sh als Erstes anlegt
# (kein Altersvergleich, Entscheidung 8). Dann tut es nichts: postinstall.sh
# spielt Zweitschriften und Rettung zurueck.
#
# Ohne Marke ist es eine NEUINSTALLATION. Was eine fruehere Installation neben
# den Ordnern liegen liess - die Zweitschriften von vw.json und zugang.json
# (Konto, Passwort, S-PIN, Aktionstoken), der Startmerker, die Fahrzeugnummern
# und die Rettung aus einem Update (Anmeldemarken, Verlauf, Ladeprotokoll) -,
# geht nach <name>.alt, gemeldet mit genau einer <WARNING>. Die gerettete
# virtuelle Umgebung wird verworfen. Bis 0.9.26 spielte postinstall.sh das alles
# zurueck, meldete "Aktualisierung abgeschlossen" und startete den Dienst, der
# sich mit dem alten Konto anmeldete (gemessen, Installerbericht N1). Hier und
# nicht erst in postinstall.sh, weil die Oberflaeche und der Minutentakt schon
# vor postinstall.sh erreichbar sind (Takt-Luecke). Die Selbstheilung der
# Bibliothek liest .alt nie; die Deinstallation raeumt es ab.

ARGV3=$3
ARGV5=$5
PFOLDER="${ARGV3:-volkswagenid}"
BASE="${ARGV5:-$LBHOMEDIR}"

# Wurzel wie in den uebrigen Haken: ohne config/plugins, data/plugins UND
# config/system/general.json wird nichts angefasst (Regeln/06).
if [ -z "$BASE" ] || [ ! -d "$BASE/config/plugins" ] || [ ! -d "$BASE/data/plugins" ] \
   || [ ! -f "$BASE/config/system/general.json" ]; then
    echo "<WARNING> Kein LoxBerry-Wurzelverzeichnis erkannt ('$BASE') - nichts beiseitegelegt."
    exit 0
fi
# Der Ordnername darf keinen Pfadtrenner tragen, sonst griffe mv/rm daneben.
case "$PFOLDER" in
    ''|*/*|*..*) echo "<WARNING> Unzulaessiger Ordnername '$PFOLDER' - nichts beiseitegelegt."; exit 0 ;;
esac

MARKE="$BASE/data/plugins/$PFOLDER.upgrade_laeuft"
if [ -f "$MARKE" ]; then
    # Aktualisierung: nichts zu tun, postinstall.sh spielt zurueck.
    exit 0
fi

BEISEITE=""
FEST=""
for ZIEL in "$BASE/config/plugins/$PFOLDER.backup.vw.json" \
            "$BASE/config/plugins/$PFOLDER.backup.zugang.json" \
            "$BASE/config/plugins/$PFOLDER.lief_vorher" \
            "$BASE/data/plugins/$PFOLDER.nummern.json" \
            "$BASE/data/plugins/$PFOLDER.rettung"; do
    if [ -e "$ZIEL" ] || [ -L "$ZIEL" ]; then
        if [ -L "$ZIEL.alt" ] || [ -f "$ZIEL.alt" ]; then
            rm -f "$ZIEL.alt"
        elif [ -d "$ZIEL.alt" ]; then
            rm -rf "${ZIEL:?}.alt"
        fi
        if mv -f "$ZIEL" "$ZIEL.alt" 2>/dev/null; then
            BEISEITE="$BEISEITE $ZIEL.alt"
        else
            FEST="$FEST $ZIEL"
        fi
    fi
done
for f in "$BASE/config/plugins/$PFOLDER.backup.vw.json.alt" "$BASE/config/plugins/$PFOLDER.backup.zugang.json.alt"; do
    [ -f "$f" ] && [ ! -L "$f" ] && chmod 600 "$f" 2>/dev/null
done
# Die gerettete virtuelle Umgebung einer frueheren Installation traegt keine
# Einstellung, aber einen fremden Stand der Bibliothek: sie wird verworfen.
VENV_ALT="$BASE/bin/plugins/$PFOLDER.venv"
if [ -L "$VENV_ALT" ]; then
    rm -f "$VENV_ALT"
elif [ -d "$VENV_ALT" ]; then
    rm -rf "${VENV_ALT:?}"
fi

if [ -n "$BEISEITE" ] || [ -n "$FEST" ]; then
    VW_TEXT="<WARNING> Neuinstallation: Einstellungen, Zugangsdaten und Bestaende einer frueheren Installation werden NICHT eingespielt, und der Dienst wird nicht gestartet."
    [ -n "$BEISEITE" ] && VW_TEXT="$VW_TEXT Beiseitegelegt:$BEISEITE (die Deinstallation raeumt sie ab)."
    [ -n "$FEST" ] && VW_TEXT="$VW_TEXT Nicht zu verschieben, bitte von Hand entfernen:$FEST"
    echo "$VW_TEXT"
fi
exit 0
