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
rm -f "$MERKER"

PID="$BASE/data/plugins/$PFOLDER/dienst.pid"
if [ -f "$PID" ]; then
    # Gefragt wird, ob der Vorgang WIRKLICH laeuft. Eine liegengebliebene
    # PID-Datei ist kein laufender Dienst - und sie darf nach dem Upgrade
    # keinen Start ausloesen, den niemand gewollt hat.
    if kill -0 "$(cat "$PID")" 2>/dev/null; then
        : > "$MERKER"
        echo "<INFO> Der Dienst lief - er wird nach dem Upgrade wieder gestartet."
    fi
    kill "$(cat "$PID")" 2>/dev/null || true
    sleep 2
    kill -9 "$(cat "$PID")" 2>/dev/null || true
    rm -f "$PID"
    echo "<INFO> Laufender Dienst angehalten."
fi

# Die Zuordnung Fahrgestellnummer -> Fahrzeugnummer liegt bereits NEBEN dem
# Datenordner und ueberlebt damit den Loeschblock. Hier wird sie nur
# gesichert, falls eine aeltere Fassung sie noch DARIN abgelegt hat.
ALTNR="$BASE/data/plugins/$PFOLDER/nummern.json"
NEUNR="$BASE/data/plugins/$PFOLDER.nummern.json"
if [ -f "$ALTNR" ] && [ ! -f "$NEUNR" ]; then
    cp -p "$ALTNR" "$NEUNR" 2>/dev/null || true
    echo "<INFO> Fahrzeugnummern gesichert."
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
