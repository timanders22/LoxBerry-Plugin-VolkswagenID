#!/bin/bash
# Volkswagen ID - postupgrade
# command <TEMPFOLDER> <NAME> <FOLDER> <VERSION> <BASEFOLDER>
#
# Nach dem Upgrade wird dieselbe Einrichtung wie bei der Erstinstallation
# gefahren. postinstall.sh legt die virtuelle Umgebung nur an, wenn sie fehlt
# oder unbrauchbar ist, und stellt die gesicherte Konfiguration zurueck.
SELF=$(cd "$(dirname "$0")" && pwd)
if [ -x "$SELF/postinstall.sh" ]; then
    "$SELF/postinstall.sh" "$@"
    exit $?
fi
echo "<FAIL> postinstall.sh nicht gefunden - Upgrade unvollstaendig."
exit 1
