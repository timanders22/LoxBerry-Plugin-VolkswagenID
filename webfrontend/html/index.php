<?php
/**
 * Volkswagen ID - Endpunkt fuer den Miniserver
 *
 * Liegt im unangemeldeten Bereich, damit Loxone ihn ohne Zugangsdaten
 * erreicht, und ist deshalb durch ein Token geschuetzt. Verglichen wird mit
 * hash_equals, also in gleichbleibender Zeit - ein einfaches == liesse sich
 * ueber die Antwortzeit Zeichen fuer Zeichen erraten.
 *
 *   /plugins/<ordner>/index.php?token=<TOKEN>&aktion=<Befehl>
 *
 * Lesende Aktionen:
 *   status   [&fahrzeug=N]   Hauptwerte des Fahrzeugs
 *   laden    [&fahrzeug=N]   Ladewerte (nur bei Elektro und Hybrid belegt)
 *   wartung  [&fahrzeug=N]   Inspektion, Oelservice, Warnleuchten
 *   position [&fahrzeug=N]   Standort
 *   fahrzeuge                Liste der erkannten Fahrzeuge
 *   roh                      vollstaendiges Abbild als JSON (Fehlersuche)
 *
 * Schaltende Aktionen (nur wenn im Reiter Einstellungen zugelassen):
 *   klima_start &temp=<Grad>    klima_stop
 *   zieltemperatur &temp=<Grad>
 *   laden_start                 laden_stop
 *   ladegrenze &prozent=<%>
 *   ladestrom &ampere=<A>
 *   scheibe_ein                 scheibe_aus
 *   wecken
 *   abruf                       sofortiger Abruf statt Warten auf den Takt
 *
 * Der Endpunkt spricht NIE selbst mit der Volkswagen-Schnittstelle. Lesende Aktionen
 * beantwortet er aus dem Zwischenspeicher, schaltende legt er in einer
 * Warteschlange ab, die der Dienst abarbeitet.
 *
 * Ein Strich als Wert bedeutet: dieser Wert liegt nicht vor. Es wird bewusst
 * keine 0 gesendet - eine 0 waere eine stille Falschaussage. Loxone behaelt
 * dann den letzten gueltigen Wert; genau das ist bei einem fehlenden Messwert
 * richtig.
 */

error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
require_once __DIR__ . '/vw_lib.php';
header('Content-Type: text/plain; charset=utf-8');

$vw_cfg = vw_config();
$vw_p = vw_paths();

/* ---------------- Token ---------------- */
$vw_soll = (string) $vw_cfg['aktionstoken'];
$vw_ist = isset($_GET['token']) ? (string) $_GET['token'] : '';
if ($vw_soll === '') {
    http_response_code(403);
    echo "FEHLER;OK=0;GRUND=KEIN_TOKEN_GESETZT\n";
    echo "Die Plugin-Oberflaeche wurde noch nie geoeffnet - es gibt noch kein Token.\n";
    exit;
}
if (!hash_equals($vw_soll, $vw_ist)) {
    http_response_code(403);
    echo "FEHLER;OK=0;GRUND=TOKEN\n";
    exit;
}

/* ---------------- Aktion (Weissliste) ---------------- */
$vw_lesend = array('status', 'laden', 'wartung', 'position', 'fahrzeuge', 'roh');
$vw_schaltend = array('klima_start', 'klima_stop', 'zieltemperatur', 'laden_start',
                      'laden_stop', 'ladegrenze', 'ladestrom', 'scheibe_ein',
                      'scheibe_aus', 'wecken', 'abruf');
$vw_aktion = isset($_GET['aktion']) ? (string) $_GET['aktion'] : 'status';
if (!in_array($vw_aktion, array_merge($vw_lesend, $vw_schaltend), true)) {
    http_response_code(400);
    echo "FEHLER;OK=0;GRUND=UNBEKANNTE_AKTION\n";
    echo 'Erlaubt sind: ' . implode(', ', array_merge($vw_lesend, $vw_schaltend)) . "\n";
    exit;
}

/* ---------------- Parameter pruefen ----------------
 * Was nicht ins Muster passt, wird abgewiesen und gemeldet. Nie Zeichen
 * entfernen, nie zurechtbiegen - ein still veraenderter Wert fuehrt zu einem
 * Fahrzeug, das etwas anderes tut, als die Adresse sagt.
 */
function vw_param($name, $muster, $vorgabe = '')
{
    if (!isset($_GET[$name]) || $_GET[$name] === '') {
        return $vorgabe;
    }
    $w = (string) $_GET[$name];
    if (!preg_match($muster, $w)) {
        http_response_code(400);
        echo "FEHLER;OK=0;GRUND=PARAMETER\n";
        echo 'Der Wert von ' . $name . " passt nicht ins erlaubte Muster.\n";
        exit;
    }
    return $w;
}

// Die laufende Nummer oder eine VIN (17 Zeichen, Buchstaben und Ziffern).
$vw_fahrzeug = vw_param('fahrzeug', '/^([0-9]{1,2}|[A-Za-z0-9]{17})$/', '1');
$vw_temp     = vw_param('temp', '/^[0-9]{1,2}([.,][05])?$/', '');
$vw_prozent  = vw_param('prozent', '/^[0-9]{1,3}$/', '');
$vw_ampere   = vw_param('ampere', '/^[0-9]{1,2}$/', '');

/* ---------------- Hilfsausgabe ---------------- */
function vw_w($v)
{
    if ($v === null || $v === '' || !is_numeric($v)) {
        return '-';
    }
    return (string) (0 + $v);
}

$vw_lox = vw_loxone();
$vw_alter = vw_alter();
$vw_ok = (!empty($vw_lox['ok']) && $vw_alter >= 0) ? 1 : 0;
$vw_alle = vw_fahrzeuge();

/** Findet das Fahrzeug zur laufenden Nummer oder zur VIN. */
function vw_waehlen($alle, $schluessel)
{
    if (isset($alle[$schluessel])) {
        return $alle[$schluessel];
    }
    foreach ($alle as $f) {
        if (isset($f['vin']) && strcasecmp((string) $f['vin'], (string) $schluessel) === 0) {
            return $f;
        }
    }
    return null;
}

/* ================= Lesende Aktionen ================= */

if ($vw_aktion === 'roh') {
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($vw_lox, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

if ($vw_aktion === 'fahrzeuge') {
    echo 'FAHRZEUGE;OK=' . $vw_ok . ';N=' . count($vw_alle) . ';ALTER=' . $vw_alter . "\n";
    foreach ($vw_alle as $vw_nr => $vw_f) {
        echo $vw_nr . ';' . (isset($vw_f['modell']) ? $vw_f['modell'] : '') . ';'
           . (isset($vw_f['kennzeichen']) ? $vw_f['kennzeichen'] : '') . ';'
           . (isset($vw_f['vin']) ? $vw_f['vin'] : '') . ';'
           . 'Ausfaelle=' . (isset($vw_f['ausfaelle']) && is_array($vw_f['ausfaelle'])
                             ? count($vw_f['ausfaelle']) : 0) . "\n";
    }
    exit;
}

$vw_f = vw_waehlen($vw_alle, $vw_fahrzeug);

if (in_array($vw_aktion, array('status', 'laden', 'wartung', 'position'), true) && $vw_f === null) {
    printf("%s;OK=0;GRUND=FAHRZEUG_UNBEKANNT;N=%d;ALTER=%d\n",
        strtoupper($vw_aktion), count($vw_alle), $vw_alter);
    exit;
}

/** Ein Wert aus dem Abbild, oder null. */
function vw_v($f, $name)
{
    return isset($f[$name]) ? $f[$name] : null;
}

if ($vw_aktion === 'status') {
    printf("VOLKSWAGEN;OK=%d;SOC=%s;TANK=%s;REICHW=%s;KM=%s;VERR=%s;TUEREN=%s;FENSTER=%s;"
         . "LICHT=%s;HANDBR=%s;KLIMA=%s;ZIELTEMP=%s;AUSSEN=%s;SCHEIBE=%s;ZUSTAND=%s;"
         . "ERREICH=%s;ALTER=%d\n",
        $vw_ok,
        vw_w(vw_v($vw_f, 'soc')), vw_w(vw_v($vw_f, 'tank_prozent')),
        vw_w(vw_v($vw_f, 'reichweite_km')), vw_w(vw_v($vw_f, 'kilometerstand')),
        vw_w(vw_v($vw_f, 'verriegelt')), vw_w(vw_v($vw_f, 'tueren_offen')),
        vw_w(vw_v($vw_f, 'fenster_offen')), vw_w(vw_v($vw_f, 'licht_an')),
        vw_w(vw_v($vw_f, 'handbremse')), vw_w(vw_v($vw_f, 'klima_an')),
        vw_w(vw_v($vw_f, 'zieltemperatur')), vw_w(vw_v($vw_f, 'aussentemperatur')),
        vw_w(vw_v($vw_f, 'scheibenheizung')), vw_w(vw_v($vw_f, 'zustand')),
        vw_w(vw_v($vw_f, 'erreichbar')), $vw_alter);
    exit;
}

if ($vw_aktion === 'laden') {
    // Der Fertigzeitpunkt kommt als Unix-Zeit aus dem Dienst. Loxone kann mit
    // einer Restzeit in Minuten mehr anfangen als mit einem Zeitstempel, also
    // wird hier umgerechnet - und nur, wenn er in der Zukunft liegt.
    $vw_fertig = vw_v($vw_f, 'laden_fertig_um');
    $vw_restmin = null;
    if (is_numeric($vw_fertig) && (int) $vw_fertig > time()) {
        $vw_restmin = (int) ceil(((int) $vw_fertig - time()) / 60);
    }
    printf("LADEN;OK=%d;SOC=%s;LAEDT=%s;LADEKW=%s;TEMPO=%s;LADEGR=%s;LADESTROM=%s;"
         . "KABEL=%s;STECKER=%s;REICHWBAT=%s;FERTIGMIN=%s;ALTER=%d\n",
        $vw_ok,
        vw_w(vw_v($vw_f, 'soc')), vw_w(vw_v($vw_f, 'laedt')),
        vw_w(vw_v($vw_f, 'ladeleistung_kw')), vw_w(vw_v($vw_f, 'ladetempo_kmh')),
        vw_w(vw_v($vw_f, 'ladegrenze')), vw_w(vw_v($vw_f, 'ladestrom_a')),
        vw_w(vw_v($vw_f, 'kabel_verbunden')), vw_w(vw_v($vw_f, 'stecker_verriegelt')),
        vw_w(vw_v($vw_f, 'reichweite_elektro_km')), vw_w($vw_restmin), $vw_alter);
    exit;
}

if ($vw_aktion === 'wartung') {
    printf("WARTUNG;OK=%d;INSPTAGE=%s;INSPKM=%s;OELTAGE=%s;OELKM=%s;KM=%s;ALTER=%d\n",
        $vw_ok,
        vw_w(vw_v($vw_f, 'inspektion_tage')), vw_w(vw_v($vw_f, 'inspektion_km')),
        vw_w(vw_v($vw_f, 'oelservice_tage')), vw_w(vw_v($vw_f, 'oelservice_km')),
        vw_w(vw_v($vw_f, 'kilometerstand')), $vw_alter);
    exit;
}

if ($vw_aktion === 'position') {
    printf("POSITION;OK=%d;BREITE=%s;LAENGE=%s;ALTER=%d\n",
        $vw_ok, vw_w(vw_v($vw_f, 'breite')), vw_w(vw_v($vw_f, 'laenge')), $vw_alter);
    // Die Anschrift steht in einer zweiten Zeile, damit die erste Zeile fuer
    // Loxone rein aus Zahlen besteht.
    echo 'ADRESSE;' . str_replace(array("\r", "\n", ';'), ' ',
        (string) vw_v($vw_f, 'adresse')) . "\n";
    exit;
}

/* ================= Schaltende Aktionen ================= */

if ($vw_aktion !== 'abruf' && empty($vw_cfg['steuerung_ein'])) {
    http_response_code(403);
    echo "SET;OK=0;GRUND=STEUERUNG_AUS\n";
    echo "Schreibende Befehle sind gesperrt. Reiter Einstellungen, Haken 'Schreibende Befehle zulassen'.\n";
    exit;
}
if (vw_dienst_pid() === 0) {
    // Nicht stillschweigend einreihen: ohne laufenden Dienst passiert nichts,
    // und der Befehl laege bis zum naechsten Start in der Warteschlange.
    http_response_code(503);
    echo "SET;OK=0;GRUND=DIENST_LAEUFT_NICHT\n";
    echo "Der Abrufdienst laeuft nicht. Reiter Einstellungen, Knopf 'Dienst starten'.\n";
    exit;
}

$vw_befehl = array('aktion' => $vw_aktion, 'fahrzeug' => $vw_fahrzeug);

if ($vw_aktion === 'klima_start' || $vw_aktion === 'zieltemperatur') {
    if ($vw_temp === '') {
        http_response_code(400);
        echo "SET;OK=0;GRUND=TEMP_FEHLT\n";
        echo "Der Parameter temp fehlt (Zieltemperatur in Grad Celsius).\n";
        exit;
    }
    $vw_befehl['temp'] = str_replace(',', '.', $vw_temp);
} elseif ($vw_aktion === 'ladegrenze') {
    if ($vw_prozent === '') {
        http_response_code(400);
        echo "SET;OK=0;GRUND=PROZENT_FEHLT\n";
        exit;
    }
    $vw_befehl['prozent'] = (int) $vw_prozent;
} elseif ($vw_aktion === 'ladestrom') {
    if ($vw_ampere === '') {
        http_response_code(400);
        echo "SET;OK=0;GRUND=AMPERE_FEHLT\n";
        echo "Der Parameter ampere fehlt (zulaessig: 5, 6, 10, 13, 16 oder 32).\n";
        exit;
    }
    $vw_befehl['ampere'] = (int) $vw_ampere;
}

list($vw_erg, $vw_meldung) = vw_befehl_absetzen($vw_befehl);
if ($vw_erg === 0) {
    http_response_code(500);
}
printf("SET;OK=%d;AKTION=%s;MELDUNG=%s\n", $vw_erg, $vw_aktion,
    str_replace(array("\r", "\n", ';'), ' ', $vw_meldung));
