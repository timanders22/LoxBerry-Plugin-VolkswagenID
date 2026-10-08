<?php
/**
 * Volkswagen ID - Ansage auf Zuruf des Abrufdienstes (Nr. 36 b, Stufe 2, seit 0.9.29)
 *
 * Aufruf:  php vw_ansage.php <Pluginordner>      (Auftrag als JSON auf der Standardeingabe)
 *
 * Der Dienst bin/vw.py ist in Python geschrieben; die gemeinsame Sprachausgabe
 * der Plugins dieses Hauses (sprachausgabe.php neben vw_lib.php) gibt es nur in
 * PHP. Diese Bruecke nimmt einen Anlass entgegen, baut den Satz aus der
 * Sprachdatei (Abschnitt VW_ANSAGE) und spricht ihn mit ansage_cli() ueber die
 * eingestellte Ausgabeart (Block tts der Konfiguration, ab Werk aus).
 *
 * Der Auftrag kommt auf der STANDARDEINGABE, nie auf der Kommandozeile - die
 * sieht jeder in der Prozessliste:
 *   {"anlass": "laden_fertig", "nr": 1, "name": "ID.3", "soc": 80, "grenze": 80}
 * Angenommen werden nur die Anlaesse aus vw_ansage_anlaesse(); der Satz entsteht
 * hier, der Dienst schickt keinen freien Text.
 *
 * Antwort: EINE Zeile ohne Text und ohne Token, z. B.
 *   ANSAGE;STAND=1;ART=musicserver;KENNUNG=-;HTTP=200;ZEICHEN=39
 * Rueckgabewert wie ansage_cli(): 0 gesendet, 1 gescheitert, 3 nichts gesendet
 * ohne Fehler (Ausgabe aus), 2 Aufruf falsch. 1 auch, wenn die Bibliothek fehlt.
 *
 * Der Pluginordner wird mitgegeben wie bei den *_notify.php-Stuecken anderer
 * Linien: dem Dienst koennen die LoxBerry-Umgebungsvariablen fehlen, und bei
 * einer Zweitinstallation heisst der Ordner volkswagenid_01.
 */

error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);

if (PHP_SAPI !== 'cli') {
    http_response_code(403);
    echo "ANSAGE;OK=0;GRUND=KEIN_ENDPUNKT\n";
    exit;
}

/* Den LoxBerry-Wurzelordner ohne festen Systempfad bestimmen - dieselbe Regel
 * wie vw_lib.php. DIESER BLOCK STEHT VOR SEINEM AUFRUF: PHP zieht Funktionen in
 * einem if-Block nicht vor. */
if (!function_exists('lb_wurzel_ermitteln')) {
    function lb_wurzel_ermitteln()
    {
        $d = __DIR__;
        for ($i = 0; $i < 8; $i++) {
            if (is_dir($d . '/config/plugins') && is_dir($d . '/data/plugins')
                && is_file($d . '/config/system/general.json')) {
                return $d;
            }
            $eltern = dirname($d);
            if ($eltern === $d) { break; }
            $d = $eltern;
        }
        return '';
    }
}

$vw_home = getenv('LBHOMEDIR');
if (!$vw_home || !is_dir($vw_home . '/config/plugins') || !is_dir($vw_home . '/data/plugins')) {
    $vw_home = lb_wurzel_ermitteln();
}
$vw_paket = isset($argv[1]) ? preg_replace('/[^A-Za-z0-9_\-]/', '', (string) $argv[1]) : '';
if ($vw_paket === '') {
    $vw_paket = preg_replace('/[^A-Za-z0-9_\-]/', '', basename(rtrim((string) getenv('LBPPLUGINDIR'), '/')));
}
if ($vw_paket === '') {
    $vw_paket = 'volkswagenid';
}

/* Die Bibliothek: installiert unter <home>/webfrontend/html/plugins/<ordner>/,
 * im Archiv unter ../webfrontend/html/. */
$vw_lib = '';
foreach (array(
    $vw_home ? $vw_home . '/webfrontend/html/plugins/' . $vw_paket . '/vw_lib.php' : '',
    dirname(__DIR__) . '/webfrontend/html/vw_lib.php',
) as $vw_kandidat) {
    if ($vw_kandidat !== '' && is_file($vw_kandidat)) {
        $vw_lib = $vw_kandidat;
        break;
    }
}
if ($vw_lib === '') {
    fwrite(STDERR, "vw_lib.php nicht gefunden - es wurde nichts angesagt.\n");
    echo "ANSAGE;STAND=0;KENNUNG=BIBLIOTHEK\n";
    exit(1);
}
/* Die Sprache des LoxBerry (Base.Lang) kennt erst LBSystem. */
if ($vw_home && is_file($vw_home . '/libs/phplib/loxberry_system.php')) {
    require_once $vw_home . '/libs/phplib/loxberry_system.php';
}
require_once $vw_lib;

$vw_roh = stream_get_contents(STDIN);
$vw_d = is_string($vw_roh) && strlen($vw_roh) <= 4096 ? json_decode($vw_roh, true) : null;
$vw_anlaesse = vw_ansage_anlaesse();
$vw_zahl = function ($w) {
    if ($w === null) { return null; }
    if (!is_int($w) && !is_float($w)) { return false; }
    $i = (int) round((float) $w);
    return ($i >= 0 && $i <= 100) ? $i : false;
};
$vw_ok = is_array($vw_d)
    && isset($vw_d['anlass']) && is_string($vw_d['anlass']) && isset($vw_anlaesse[$vw_d['anlass']])
    && isset($vw_d['nr']) && is_int($vw_d['nr']) && $vw_d['nr'] >= 0 && $vw_d['nr'] <= 99
    && (!isset($vw_d['name']) || is_string($vw_d['name']));
$vw_soc = $vw_ok ? $vw_zahl(isset($vw_d['soc']) ? $vw_d['soc'] : null) : false;
$vw_grenze = $vw_ok ? $vw_zahl(isset($vw_d['grenze']) ? $vw_d['grenze'] : null) : false;
if (!$vw_ok || $vw_soc === false || $vw_grenze === false) {
    fwrite(STDERR, "Auftrag fehlt, ist kein JSON oder nennt einen unbekannten Anlass.\n");
    echo "ANSAGE;STAND=0;KENNUNG=AUFRUF\n";
    exit(2);
}
/* Der Fahrzeugname kommt aus dem Konto; nur Steuerzeichen fallen weg, und er
 * wird auf 60 Zeichen begrenzt - er wird gesprochen, nicht gespeichert. */
$vw_name = isset($vw_d['name']) ? trim((string) preg_replace('/[\x00-\x1F\x7F]/u', ' ', $vw_d['name'])) : '';
if (preg_match('//u', $vw_name) !== 1) {
    $vw_name = '';
}
if (function_exists('mb_substr')) {
    $vw_name = mb_substr($vw_name, 0, 60, 'UTF-8');
} elseif (strlen($vw_name) > 60) {
    $vw_name = '';
}

$vw_text = vw_ansage_satz($vw_d['anlass'], $vw_d['nr'], $vw_name, $vw_soc, $vw_grenze);
list($vw_rc, $vw_zeile) = ansage_cli(json_encode(array('text' => $vw_text), JSON_UNESCAPED_UNICODE),
                                     vw_tts(false), vw_ansage_k());
echo $vw_zeile, "\n";
exit($vw_rc);
