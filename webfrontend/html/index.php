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
 *   status    [&fahrzeug=N]   Hauptwerte samt Lebenszeichen
 *   laden     [&fahrzeug=N]   Ladewerte (nur bei Elektro und Hybrid belegt)
 *   wartung   [&fahrzeug=N]   Inspektion, Oelservice, AdBlue
 *   position  [&fahrzeug=N]   Standort, Entfernung zum Heimatort
 *   verbrauch [&fahrzeug=N]   Fahrverbrauch und Ladebilanz
 *   teile     [&fahrzeug=N]   offene Tueren und Fenster, mit Namen
 *   fahrzeuge                 Liste der erkannten Fahrzeuge
 *   roh                       vollstaendiges Abbild als JSON (Fehlersuche)
 *
 * Schaltende Aktionen (nur wenn im Reiter Einstellungen zugelassen):
 *   klima_start &temp=<Grad>    klima_stop
 *   zieltemperatur &temp=<Grad>
 *   laden_start                 laden_stop
 *   ladegrenze &prozent=<%>
 *   ladestrom &ampere=<A>
 *   scheibe_ein                 scheibe_aus
 *   wecken
 *   einstellung &name=<Name>&wert=<0|1>
 *   abruf                       sofortiger Abruf statt Warten auf den Takt
 *
 * Eingreifende Aktionen (zusaetzlich der zweite Haken, ab Werk aus):
 *   verriegeln                  entriegeln      (beide brauchen die S-PIN)
 *   blinken                     hupen
 *
 * Der Endpunkt spricht NIE selbst mit der Volkswagen-Schnittstelle. Lesende
 * Aktionen beantwortet er aus dem Zwischenspeicher, schaltende legt er in
 * einer Warteschlange ab, die der Dienst abarbeitet.
 *
 * Er SCHREIBT auch sonst nichts: vw_config(false) schaltet die Selbstheilung
 * ab. Bis 0.9.9 hat ein einziger Aufruf ohne Token - korrekt mit 403
 * beantwortet - die Konfigurationsdatei aus der Zweitschrift zurueckgeschrieben
 * (gemessen am 27.08.2026). Wer sich nicht ausweisen kann, legt nichts an.
 *
 * Ein Strich als Wert bedeutet: dieser Wert liegt nicht vor. Es wird bewusst
 * keine 0 gesendet - eine 0 waere eine stille Falschaussage. Loxone behaelt
 * dann den letzten gueltigen Wert; genau das ist bei einem fehlenden Messwert
 * richtig.
 *
 * ZAEHLER und ALTER gehoeren auf eine Ueberwachung. ALTER uebersteht keinen
 * Zeitsprung - ein Raspberry ohne Echtzeituhr steht nach dem Booten in der
 * Vergangenheit, und sobald NTP greift, springt die Zeit. Der umlaufende
 * Zaehler ist davon unabhaengig; in Loxone genuegt ein Baustein, der auf
 * "unveraendert seit N Minuten" schaut.
 */

error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
require_once __DIR__ . '/vw_lib.php';
header('Content-Type: text/plain; charset=utf-8');

/* NICHTS ANLEGEN: der unangemeldete Endpunkt liest nur. */
$vw_cfg = vw_config(false);
$vw_p = vw_paths();

/* ---------------- Token ---------------- */
$vw_soll = (string) $vw_cfg['aktionstoken'];
$vw_ist = isset($_GET['token']) && is_string($_GET['token']) ? (string) $_GET['token'] : '';
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

/* ---------------- Aktion (Weissliste) ----------------
 * Die schaltenden Aktionen stammen aus vw_befehle() - derselben Quelle, aus
 * der die Tabelle im Reiter Loxone und die Ausgangsvorlage entstehen. Bis
 * 0.9.9 standen sie hier woertlich, und die Tabelle nannte drei davon nicht. */
$vw_lesend = array('status', 'laden', 'wartung', 'position', 'verbrauch', 'teile',
                   'fahrzeuge', 'roh');
$vw_alle_befehle = vw_befehle();
$vw_schaltend = array_keys($vw_alle_befehle);
$vw_aktion = isset($_GET['aktion']) && is_string($_GET['aktion']) ? (string) $_GET['aktion'] : 'status';
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
 *
 * is_string() zuerst: ?temp[]=1 macht aus $_GET['temp'] ein Feld, und
 * preg_match auf ein Feld ist in PHP 8 ein TypeError.
 */
function vw_param($name, $muster, $vorgabe = '')
{
    if (!isset($_GET[$name]) || !is_string($_GET[$name]) || $_GET[$name] === '') {
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
$vw_name     = vw_param('name', '/^[a-z_]{1,32}$/', '');
$vw_wert     = vw_param('wert', '/^[01]$/', '');

/* ---------------- Hilfsausgabe ---------------- */
function vw_w($v)
{
    if ($v === null || $v === '' || !is_numeric($v)) {
        return '-';
    }
    return (string) (0 + $v);
}

/** Eine Zeichenkette fuer die Ausgabe entschaerfen: keine Trenner, keine Umbrueche. */
function vw_s($v, $laenge = 120)
{
    $s = str_replace(array("\r", "\n", "\t", ';'), ' ', (string) $v);
    $s = trim(preg_replace('/\s+/', ' ', $s));
    return $s === '' ? '-' : substr($s, 0, $laenge);
}

$vw_lox = vw_loxone();
$vw_alter = vw_alter();
$vw_ok = (!empty($vw_lox['ok']) && $vw_alter >= 0) ? 1 : 0;
$vw_alle = vw_fahrzeuge();

/* Das Lebenszeichen des Dienstes. Es haengt NICHT am Abbild: ein Abruf kann
 * fehlschlagen, waehrend der Dienst tadellos arbeitet. -1 heisst "noch nie
 * gelaufen"; 0 waere ein gueltiger Stand und deshalb keine Antwort. */
$vw_zust = vw_zustand();
$vw_zaehler = isset($vw_zust['zaehler']) && is_numeric($vw_zust['zaehler'])
            ? (int) $vw_zust['zaehler'] : -1;
$vw_fehlfolge = isset($vw_zust['fehler_folge']) && is_numeric($vw_zust['fehler_folge'])
              ? (int) $vw_zust['fehler_folge'] : 0;
$vw_grund = isset($vw_lox['grund']) && $vw_lox['grund'] !== '' ? (string) $vw_lox['grund']
          : ($vw_ok ? 'OK' : 'KEIN_ABRUF');
$vw_fehlertext = isset($vw_lox['fehler']) ? (string) $vw_lox['fehler'] : '';

/**
 * Findet die laufende Nummer zur Nummer oder zur VIN. 0 = nicht gefunden.
 *
 * Abgewiesen, nicht zurechtgebogen: eine VIN mit einem Tippfehler ergibt 0
 * und damit FAHRZEUG_UNBEKANNT. Der schreibende Weg im Dienst hat bis 0.9.9
 * an derselben Frage auf Fahrzeug 1 zurueckgegriffen - bei zwei Fahrzeugen
 * startete die Klimatisierung am falschen Auto und meldete OK=1.
 */
function vw_nummer_von($alle, $schluessel)
{
    if (isset($alle[$schluessel])) {
        return (int) $schluessel;
    }
    foreach ($alle as $nr => $f) {
        if (isset($f['vin']) && $f['vin'] !== ''
            && strcasecmp((string) $f['vin'], (string) $schluessel) === 0) {
            return (int) $nr;
        }
    }
    return 0;
}

/** Findet das Fahrzeug zur laufenden Nummer oder zur VIN. */
function vw_waehlen($alle, $schluessel)
{
    $nr = vw_nummer_von($alle, $schluessel);
    return $nr > 0 && isset($alle[(string) $nr]) ? $alle[(string) $nr] : null;
}

/**
 * Die zweite Zeile: Grund und Klartext der letzten Stoerung.
 *
 * Sie steht in einer eigenen Zeile, damit die erste rein aus Zahlen besteht -
 * dieselbe Bauart wie bei POSITION und ADRESSE. Ein Textfeld gehoert nicht in
 * die Statuszeile; ein Semikolon darin zerlegte jede Befehlserkennung.
 */
function vw_meldezeile($grund, $text)
{
    echo 'MELDUNG;GRUND=' . vw_s($grund, 40) . ';TEXT=' . vw_s($text, 200) . "\n";
}

/* ================= Lesende Aktionen ================= */

if ($vw_aktion === 'roh') {
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode($vw_lox, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

if ($vw_aktion === 'fahrzeuge') {
    echo 'FAHRZEUGE;OK=' . $vw_ok . ';N=' . count($vw_alle) . ';ALTER=' . $vw_alter
       . ';ZAEHLER=' . $vw_zaehler . "\n";
    foreach ($vw_alle as $vw_nr => $vw_f) {
        echo $vw_nr . ';' . vw_s(isset($vw_f['modell']) ? $vw_f['modell'] : '', 40) . ';'
           . vw_s(isset($vw_f['kennzeichen']) ? $vw_f['kennzeichen'] : '', 20) . ';'
           . vw_s(isset($vw_f['vin']) ? $vw_f['vin'] : '', 20) . ';'
           . 'Ausfaelle=' . (isset($vw_f['ausfaelle']) && is_array($vw_f['ausfaelle'])
                             ? count($vw_f['ausfaelle']) : 0) . "\n";
    }
    exit;
}

$vw_f = vw_waehlen($vw_alle, $vw_fahrzeug);

if (in_array($vw_aktion, array('status', 'laden', 'wartung', 'position', 'verbrauch', 'teile'), true)
    && $vw_f === null) {
    printf("%s;OK=0;GRUND=FAHRZEUG_UNBEKANNT;N=%d;ALTER=%d;ZAEHLER=%d\n",
        strtoupper($vw_aktion), count($vw_alle), $vw_alter, $vw_zaehler);
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
         . "ERREICH=%s;ALTER=%d;ZAEHLER=%d;FEHLFOLGE=%d;ZUHAUSE=%s;ENTFERNUNG=%s;"
         . "STANDZEIT=%s;SITZHEIZ=%s;KLIMAENTR=%s;BATTTEMP=%s;VERBRAUCH=%s;"
         . "REICHWWLTP=%s;TUERENZAHL=%s;FENSTERZAHL=%s\n",
        $vw_ok,
        vw_w(vw_v($vw_f, 'soc')), vw_w(vw_v($vw_f, 'tank_prozent')),
        vw_w(vw_v($vw_f, 'reichweite_km')), vw_w(vw_v($vw_f, 'kilometerstand')),
        vw_w(vw_v($vw_f, 'verriegelt')), vw_w(vw_v($vw_f, 'tueren_offen')),
        vw_w(vw_v($vw_f, 'fenster_offen')), vw_w(vw_v($vw_f, 'licht_an')),
        vw_w(vw_v($vw_f, 'handbremse')), vw_w(vw_v($vw_f, 'klima_an')),
        vw_w(vw_v($vw_f, 'zieltemperatur')), vw_w(vw_v($vw_f, 'aussentemperatur')),
        vw_w(vw_v($vw_f, 'scheibenheizung')), vw_w(vw_v($vw_f, 'zustand')),
        vw_w(vw_v($vw_f, 'erreichbar')), $vw_alter, $vw_zaehler, $vw_fehlfolge,
        vw_w(vw_v($vw_f, 'zuhause')), vw_w(vw_v($vw_f, 'entfernung_m')),
        vw_w(vw_v($vw_f, 'standzeit_min')), vw_w(vw_v($vw_f, 'sitzheizung_ein')),
        vw_w(vw_v($vw_f, 'klima_bei_entriegeln')), vw_w(vw_v($vw_f, 'batterie_temp')),
        vw_w(vw_v($vw_f, 'verbrauch')), vw_w(vw_v($vw_f, 'reichweite_wltp_km')),
        vw_w(vw_v($vw_f, 'tueren_zahl')), vw_w(vw_v($vw_f, 'fenster_zahl')));
    vw_meldezeile($vw_grund, $vw_fehlertext);
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
    // Die Ladeart als Zahl: 0 unbekannt, 1 Wechselstrom, 2 Gleichstrom.
    // Der Text steht ueber MQTT und im Rohabbild; eine Statuszeile bleibt
    // rein aus Zahlen.
    $vw_art = strtolower((string) vw_v($vw_f, 'ladeart'));
    $vw_artzahl = ($vw_art === '' ? null : (strpos($vw_art, 'dc') !== false ? 2
                  : (strpos($vw_art, 'ac') !== false ? 1 : 0)));
    printf("LADEN;OK=%d;SOC=%s;LAEDT=%s;LADEKW=%s;TEMPO=%s;LADEGR=%s;LADESTROM=%s;"
         . "KABEL=%s;STECKER=%s;REICHWBAT=%s;FERTIGMIN=%s;ALTER=%d;LADEART=%s;"
         . "STECKERAUTO=%s;LADEMAXKW=%s;BATTKWH=%s;BATTTEMP=%s;EMPFEHLUNG=%s\n",
        $vw_ok,
        vw_w(vw_v($vw_f, 'soc')), vw_w(vw_v($vw_f, 'laedt')),
        vw_w(vw_v($vw_f, 'ladeleistung_kw')), vw_w(vw_v($vw_f, 'ladetempo_kmh')),
        vw_w(vw_v($vw_f, 'ladegrenze')), vw_w(vw_v($vw_f, 'ladestrom_a')),
        vw_w(vw_v($vw_f, 'kabel_verbunden')), vw_w(vw_v($vw_f, 'stecker_verriegelt')),
        vw_w(vw_v($vw_f, 'reichweite_elektro_km')), vw_w($vw_restmin), $vw_alter,
        vw_w($vw_artzahl), vw_w(vw_v($vw_f, 'stecker_entriegeln')),
        vw_w(vw_v($vw_f, 'ladesaeule_kw')), vw_w(vw_v($vw_f, 'batterie_kwh')),
        vw_w(vw_v($vw_f, 'batterie_temp')), vw_w(vw_v($vw_f, 'ladeempfehlung')));
    echo 'LADESAEULE;NAME=' . vw_s(vw_v($vw_f, 'ladesaeule_name'), 60)
       . ';BETREIBER=' . vw_s(vw_v($vw_f, 'ladesaeule_betreiber'), 60) . "\n";
    exit;
}

if ($vw_aktion === 'wartung') {
    printf("WARTUNG;OK=%d;INSPTAGE=%s;INSPKM=%s;OELTAGE=%s;OELKM=%s;KM=%s;ALTER=%d;"
         . "ADBLUEKM=%s;OELSTAND=%s\n",
        $vw_ok,
        vw_w(vw_v($vw_f, 'inspektion_tage')), vw_w(vw_v($vw_f, 'inspektion_km')),
        vw_w(vw_v($vw_f, 'oelservice_tage')), vw_w(vw_v($vw_f, 'oelservice_km')),
        vw_w(vw_v($vw_f, 'kilometerstand')), $vw_alter,
        vw_w(vw_v($vw_f, 'adblue_km')), vw_w(vw_v($vw_f, 'oelstand_prozent')));
    exit;
}

if ($vw_aktion === 'position') {
    printf("POSITION;OK=%d;BREITE=%s;LAENGE=%s;ALTER=%d;ENTFERNUNG=%s;ZUHAUSE=%s;HOEHE=%s\n",
        $vw_ok, vw_w(vw_v($vw_f, 'breite')), vw_w(vw_v($vw_f, 'laenge')), $vw_alter,
        vw_w(vw_v($vw_f, 'entfernung_m')), vw_w(vw_v($vw_f, 'zuhause')),
        vw_w(vw_v($vw_f, 'hoehe')));
    // Die Anschrift steht in einer zweiten Zeile, damit die erste Zeile fuer
    // Loxone rein aus Zahlen besteht.
    echo 'ADRESSE;' . vw_s(vw_v($vw_f, 'adresse'), 200) . "\n";
    exit;
}

if ($vw_aktion === 'verbrauch') {
    $vw_l = vw_ladungen_lesen(vw_nummer_von($vw_alle, $vw_fahrzeug), 400);
    $vw_letzt = $vw_l ? $vw_l[0] : null;
    $vw_min = null;
    $vw_vorstd = null;
    if ($vw_letzt && $vw_letzt['ende'] > $vw_letzt['start']) {
        $vw_min = (int) round(($vw_letzt['ende'] - $vw_letzt['start']) / 60);
        $vw_vorstd = (int) round((time() - $vw_letzt['ende']) / 3600);
    }
    // Die Tagesbilanz zaehlt nur, was HEUTE beendet wurde. Ein Ladevorgang,
    // der ueber Mitternacht laeuft, zaehlt zum Tag seines Endes - sonst
    // zaehlte er zweimal oder gar nicht.
    $vw_tag0 = strtotime('today 00:00');
    // Auch nach OBEN begrenzt. Eine Zeile, deren Endzeitpunkt durch einen
    // Uhrensprung in der Zukunft liegt, uebersprang bis 0.9.11 jede kuenftige
    // Tagesschwelle und zaehlte an jedem Tag mit. Die Dauerspalte war gegen
    // denselben Fall abgesichert, die Tagesbilanz nicht.
    $vw_bis = time() + 300;
    $vw_tagkwh = 0.0;
    $vw_hat = false;
    foreach ($vw_l as $vw_e) {
        if ($vw_e['ende'] >= $vw_tag0 && $vw_e['ende'] <= $vw_bis && $vw_e['kwh'] !== null) {
            $vw_tagkwh += $vw_e['kwh'];
            $vw_hat = true;
        }
    }
    printf("VERBRAUCH;OK=%d;VERBRAUCH=%s;LETZTKWH=%s;LETZTMIN=%s;LETZTVOR=%s;"
         . "LETZTNACH=%s;LETZTVORSTD=%s;TAGKWH=%s;LADUNGEN=%d;ALTER=%d\n",
        $vw_ok,
        vw_w(vw_v($vw_f, 'verbrauch')),
        vw_w($vw_letzt ? $vw_letzt['kwh'] : null), vw_w($vw_min),
        vw_w($vw_letzt ? $vw_letzt['soc_vor'] : null),
        vw_w($vw_letzt ? $vw_letzt['soc_nach'] : null), vw_w($vw_vorstd),
        vw_w($vw_hat ? round($vw_tagkwh, 2) : null),
        vw_ladungen_zahl(vw_nummer_von($vw_alle, $vw_fahrzeug)), $vw_alter);
    exit;
}

if ($vw_aktion === 'teile') {
    printf("TEILE;OK=%d;TUEREN=%s;FENSTER=%s;TUERENZAHL=%s;FENSTERZAHL=%s;LICHT=%s;ALTER=%d\n",
        $vw_ok, vw_w(vw_v($vw_f, 'tueren_offen')), vw_w(vw_v($vw_f, 'fenster_offen')),
        vw_w(vw_v($vw_f, 'tueren_zahl')), vw_w(vw_v($vw_f, 'fenster_zahl')),
        vw_w(vw_v($vw_f, 'licht_an')), $vw_alter);
    echo 'OFFEN;TUEREN=' . vw_s(vw_v($vw_f, 'tueren_namen'), 120)
       . ';FENSTER=' . vw_s(vw_v($vw_f, 'fenster_namen'), 120) . "\n";
    exit;
}

/* ================= Schaltende Aktionen ================= */

$vw_b = isset($vw_alle_befehle[$vw_aktion]) ? $vw_alle_befehle[$vw_aktion] : array();

/* Der Endpunkt prueft die ANFRAGE, bevor er den Dienst prueft. Sonst
 * beantwortet er eine gesperrte Aktion mit "Dienst laeuft nicht" - und der
 * Anwender sucht am falschen Ende. */
if ($vw_aktion !== 'abruf' && empty($vw_cfg['steuerung_ein'])) {
    http_response_code(403);
    echo "SET;OK=0;GRUND=STEUERUNG_AUS\n";
    echo "Schreibende Befehle sind gesperrt. Reiter Einstellungen, Haken 'Schreibende Befehle zulassen'.\n";
    exit;
}
/* Der zweite Haken. Ver- und Entriegeln, Hupe und Lichthupe oeffnen das
 * Fahrzeug beziehungsweise machen es auffindbar - sie haengen deshalb an einem
 * eigenen Schalter, der ab Werk aus ist. */
if (!empty($vw_b['eingreifend']) && empty($vw_cfg['eingreifend_ein'])) {
    http_response_code(403);
    echo "SET;OK=0;GRUND=EINGREIFEND_AUS\n";
    echo "Eingreifende Befehle sind gesperrt. Reiter Einstellungen, Haken 'Eingreifende Befehle zulassen'.\n";
    exit;
}
if (!empty($vw_b['spin'])) {
    $vw_zg = vw_zugang();
    if ($vw_zg['spin_laenge'] !== 4) {
        http_response_code(400);
        echo "SET;OK=0;GRUND=SPIN_FEHLT\n";
        echo "Dieser Befehl braucht die vierstellige S-PIN. Reiter Einstellungen.\n";
        exit;
    }
}
if (vw_dienst_pid() === 0) {
    // Nicht stillschweigend einreihen: ohne laufenden Dienst passiert nichts,
    // und der Befehl laege bis zum naechsten Start in der Warteschlange.
    http_response_code(503);
    echo "SET;OK=0;GRUND=DIENST_LAEUFT_NICHT\n";
    echo "Der Abrufdienst laeuft nicht. Reiter Einstellungen, Knopf 'Dienst starten'.\n";
    exit;
}

$vw_befehl = array('aktion' => $vw_aktion, 'fahrzeug' => $vw_fahrzeug, 'von' => 'endpunkt');

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
} elseif ($vw_aktion === 'einstellung') {
    $vw_schalter = vw_schalter();
    if ($vw_name === '' || !isset($vw_schalter[$vw_name])) {
        http_response_code(400);
        echo "SET;OK=0;GRUND=NAME_UNBEKANNT\n";
        echo 'Der Parameter name fehlt oder ist unbekannt. Erlaubt sind: '
           . implode(', ', array_keys($vw_schalter)) . "\n";
        exit;
    }
    if ($vw_wert === '') {
        http_response_code(400);
        echo "SET;OK=0;GRUND=WERT_FEHLT\n";
        echo "Der Parameter wert fehlt (0 oder 1).\n";
        exit;
    }
    $vw_befehl['name'] = $vw_name;
    $vw_befehl['wert'] = (int) $vw_wert;
}

/* Die Wartezeit des Endpunkts ist eine EIGENE, kuerzere als die der
 * Oberflaeche. Ein Virtueller Ausgang in Loxone haengt so lange, wie hier
 * gewartet wird; bis 0.9.9 waren das dieselben bis zu 30 Sekunden wie am
 * Bildschirm, und der Hilfetext sprach nur von der Oberflaeche. */
list($vw_erg, $vw_meldung) = vw_befehl_absetzen($vw_befehl, (int) $vw_cfg['wartezeit_endpunkt']);
if ($vw_erg === 0) {
    http_response_code(500);
}
printf("SET;OK=%d;AKTION=%s;MELDUNG=%s\n", $vw_erg, $vw_aktion, vw_s($vw_meldung, 200));
