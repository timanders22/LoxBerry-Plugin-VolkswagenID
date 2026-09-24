#!/bin/bash
# Volkswagen ID - postupgrade
# command <TEMPFOLDER-KENNUNG> <NAME> <FOLDER> <VERSION> <BASEFOLDER> <WORKDIR>
#
# ---------------------------------------------------------------------------
# WARUM HIER FAST NICHTS MEHR STEHT
#
# Bis 0.9.0 rief diese Datei postinstall.sh auf. Das sah nach Sorgfalt aus,
# war aber eine Verdopplung: der LoxBerry-Installer fuehrt postinstall OHNE
# Bedingung aus (sbin/plugininstall.pl, Abschnitt "Executing postinstall
# script" - kein if ($isupgrade) davor) und postupgrade danach ZUSAETZLICH
# beim Upgrade. postinstall lief also zweimal.
#
# Das ist nicht bloss unschoen: postinstall.sh legt die virtuelle Umgebung an
# und holt carconnectivity samt Volkswagen-Connector ueber pip aus dem Netz.
# Auf einem Raspberry Pi dauert das Minuten - und es geschah bei jedem
# Upgrade doppelt.
#
# Was ein Upgrade zusaetzlich braucht, steht hier. Alles andere hat
# postinstall.sh zu diesem Zeitpunkt bereits erledigt, einschliesslich des
# Zurueckspielens der gesicherten Konfiguration.
# ---------------------------------------------------------------------------

ARGV3=$3
ARGV5=$5
PFOLDER="${ARGV3:-volkswagenid}"
BASE="${ARGV5:-$LBHOMEDIR}"
# Bis 0.9.23 waren es zwei feste Ebenen ($SELF/../..). Dieses Skript liegt
# beim Einbau im Auspackordner; zwei Ebenen darueber ist irgendein
# Verzeichnis. Gemessen (Pruefung-VolkswagenID-0.9.24, Fall K9): in einem
# fremden Baum entfernte postupgrade.sh dort __pycache__ und die Marke.
# Deshalb aufwaerts suchen (config/plugins, data/plugins UND
# config/system/general.json, Regeln/06); ohne Wurzel wird gewarnt statt
# vollzogen.
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
    echo "<WARNING> config/system/general.json. Es wurde nichts entfernt; eine"
    echo "<WARNING> liegengebliebene Upgrade-Marke gilt nach 3600 s ohnehin nicht mehr."
    exit 1
fi

PBIN="$BASE/bin/plugins/$PFOLDER"

# Alte Python-Zwischendateien wegraeumen. Eine .pyc, die aelter ist als der
# Quelltext daneben, kann im ungluecklichen Fall statt des neuen Codes
# geladen werden. Der Ordner wird bei Bedarf neu und passend zur laufenden
# Python-Fassung angelegt.
if [ -d "$PBIN/__pycache__" ]; then
    rm -rf "$PBIN/__pycache__"
    echo "<OK> Alte Python-Zwischendateien entfernt."
fi

# ---------- Die Upgrade-Marke aus preupgrade.sh wegraeumen ----------
#
# Dies ist das LETZTE Hakenskript dieser Linie - ein postroot.sh gibt es
# nicht (Reihenfolge nach Regeln/06: preroot, preinstall, preupgrade,
# postinstall, postupgrade, postroot).
#
# Die Marke faellt HIER und nicht frueher: postinstall.sh hat den Dienst
# bereits mit VW_START_TROTZ_MARKE=1 gestartet, und solange sie liegt,
# startet kein anderer Weg einen zweiten daneben. Bleibt sie liegen -
# abgebrochene Installation -, gilt sie nach 3600 s ohnehin nicht mehr;
# bin/dienst.sh und die Oberflaeche rechnen das nach.
MARKE="$BASE/data/plugins/$PFOLDER.upgrade_laeuft"
if [ -f "$MARKE" ]; then
    rm -f "$MARKE"
    # Die Wirkung pruefen, nicht den Rueckgabewert (Kernschicht 2).
    if [ -f "$MARKE" ]; then
        echo "<WARNING> Die Marke $MARKE liess sich nicht entfernen; Dienst und"
        echo "<WARNING> Oberflaeche sind erst wieder frei, wenn sie aelter als eine Stunde ist."
    else
        echo "<OK> Aktualisierung abgemeldet."
    fi
fi

echo "<OK> postupgrade abgeschlossen."
exit 0
