<?php
/**
 * Volkswagen ID - Bedienoberflaeche
 *
 * Reiter: Einstellungen | MQTT | Einbindung in Loxone | Test | Logdateien
 *
 * Diese Datei ist NUR Oberflaeche. Der Datenabruf laeuft im Dienst
 * (bin/vw.py), der Miniserver spricht mit webfrontend/html/index.php.
 * Ein Plugin, das den Abruf hier erledigt, ist falsch gebaut - auch wenn es
 * funktioniert.
 *
 * Praefix 'vw_', weil LBWeb::lbheader() SDK-Globale setzt (unter anderem $cfg
 * aus der general.json als stdClass) und gleichnamige Plugin-Variablen
 * ueberschreiben wuerde.
 * Kompatibel mit PHP 7.4 und PHP 8.x (LoxBerry 3.x/4.x).
 */

error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);

/* Bibliothek einbinden. Sie liegt unter webfrontend/html/, weil der
 * Miniserver-Endpunkt sie ebenfalls braucht - installiert unter
 * .../html/plugins/<ordner>/, im Archiv unter ../html/. */
$vw_gefunden = false;
foreach (array(
    // installiert: <home>/webfrontend/htmlauth/plugins/<ordner>  ->
    //              <home>/webfrontend/html/plugins/<ordner>
    dirname(dirname(__DIR__)) . '/html/plugins/' . basename(__DIR__) . '/vw_lib.php',
    dirname(dirname(dirname(__DIR__))) . '/html/plugins/' . basename(__DIR__) . '/vw_lib.php',
    // im Archiv: <plugin>/webfrontend/htmlauth -> <plugin>/webfrontend/html
    dirname(__DIR__) . '/html/vw_lib.php',
) as $vw_kandidat) {
    if (is_file($vw_kandidat)) {
        require_once $vw_kandidat;
        $vw_gefunden = true;
        break;
    }
}
if (!$vw_gefunden) {
    echo '<p><b>Fehler:</b> vw_lib.php wurde nicht gefunden. Bitte das Plugin neu installieren.</p>';
    exit;
}
require_once __DIR__ . '/vw_test.php';

$vw_p = vw_paths();
if ($vw_p['home'] !== '' && is_file($vw_p['home'] . '/libs/phplib/loxberry_system.php')) {
    require_once $vw_p['home'] . '/libs/phplib/loxberry_system.php';
    require_once $vw_p['home'] . '/libs/phplib/loxberry_web.php';
}

/* Aktiver Reiter. Wer einen Reiter hinzufuegt, muss diese Positivliste
 * mitziehen - sonst springt die Seite nach jedem Absenden zurueck auf
 * Einstellungen, obwohl der Reiter sichtbar und anklickbar ist. */
/* EINE Quelle fuer Reihenfolge, Positivliste und Beschriftung.
 *
 * Bis 0.9.0 standen die Reiternamen an drei Stellen: in diesem Muster, in
 * der Reiterleiste und in den fuenf Flaechen-ids. Wer einen Reiter ergaenzt
 * und eine davon vergisst, bekommt keinen Fehler, sondern eine Seite, die
 * nach jedem Absenden auf Einstellungen zurueckspringt. */
$vw_reiter_ids = array('settings', 'mqtt', 'loxone', 'test', 'log');
$vw_muster = '/^tab-(' . implode('|', $vw_reiter_ids) . ')$/';
$vw_tab = 'tab-settings';
if (isset($_POST['activetab']) && preg_match($vw_muster, (string) $_POST['activetab'])) {
    $vw_tab = (string) $_POST['activetab'];
} elseif (isset($_GET['form']) && preg_match($vw_muster, 'tab-' . (string) $_GET['form'])) {
    $vw_tab = 'tab-' . (string) $_GET['form'];
}

$vw_meldungen = array();   // Erfolgsmeldungen
$vw_fehler = array();      // Beanstandungen - gesammelt, nicht ueberschrieben
$vw_testausgabe = '';
$vw_post = (isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '') === 'POST';

/* ==================================================================
 * DIE HANDLER STEHEN VOR lbheader() - DAS IST BAUVORSCHRIFT
 * ==================================================================
 *
 * Stand der Kopf davor, war er beim Aufruf von header() schon
 * geschrieben - "Cannot modify header information", und der Knopf
 * "Einstellungen sichern" lieferte eine Seite mit angehaengtem JSON
 * statt einer Datei.
 *
 * Am PHP-CLI ist das unsichtbar: header() ist dort wirkungslos und
 * headers_sent() immer falsch. Und wer OHNE gueltiges Formularmerkmal
 * misst, wird vom Wachposten abgewiesen, bevor der Handler anlaeuft.
 * Beides hat den Fehler lange verdeckt.
 *
 * Reihenfolge: Bibliothek, Konfiguration, Wachposten, Reiterwahl,
 * ALLE Handler samt Downloads, dann erst lbheader(), dann HTML.
 * ================================================================== */
/* ---------------- Vorlage herunterladen ---------------- */
if ($vw_post && isset($_POST['vorlage'])) {
    $vw_nr = preg_match('/^[0-9]{1,2}$/', (string) $_POST['vorlage']) ? (int) $_POST['vorlage'] : 1;
    list($vw_name, $vw_inhalt) = vw_vorlage($vw_nr);
    header('Content-Type: application/xml; charset=utf-8');
    header('Content-Disposition: attachment; filename="' . $vw_name . '"');
    echo $vw_inhalt;
    exit;
}

/* ---------------- Einstellungen speichern ---------------- */
if ($vw_post && isset($_POST['speichern'])) {
    $vw_cfg = vw_config();

    foreach (array(
        // Untergrenze 180 s: der Volkswagen-Connector wirft darunter beim
        // Anlegen einen ValueError. Lieber hier abweisen als dort abstuerzen.
        'intervall'    => array(180, 3600),
        'takt_wartung' => array(1, 240),
        'temp_min'     => array(10, 30),
        'temp_max'     => array(10, 30),
        'verlauf_tage' => array(1, 90),
        'wartezeit'    => array(0, 30),
    ) as $vw_feld => $vw_grenzen) {
        $vw_wert = isset($_POST[$vw_feld]) ? trim((string) $_POST[$vw_feld]) : '';
        if (!preg_match('/^[0-9]+$/', $vw_wert)) {
            $vw_fehler[] = sprintf(vw_t('EINST.FEHLER_ZAHL'), vw_t('EINST.L_' . strtoupper($vw_feld)));
            continue;
        }
        $vw_zahl = (int) $vw_wert;
        if ($vw_zahl < $vw_grenzen[0] || $vw_zahl > $vw_grenzen[1]) {
            $vw_fehler[] = sprintf(vw_t('EINST.FEHLER_BEREICH'),
                vw_t('EINST.L_' . strtoupper($vw_feld)), $vw_grenzen[0], $vw_grenzen[1]);
            continue;
        }
        $vw_cfg[$vw_feld] = $vw_zahl;
    }
    if (isset($vw_cfg['temp_min'], $vw_cfg['temp_max'])
        && $vw_cfg['temp_min'] > $vw_cfg['temp_max']) {
        $vw_fehler[] = vw_t('EINST.FEHLER_TEMP_TAUSCH');
    }

    $vw_cfg['steuerung_ein'] = isset($_POST['steuerung_ein']) ? 1 : 0;
    $vw_cfg['zugriff_erzwingen'] = isset($_POST['zugriff_erzwingen']) ? 1 : 0;


    /* Zugangsdaten: eigene Datei mit Rechten 0600. Ein leer zurueckgegebenes
     * Passwortfeld loescht nichts - sonst stuende irgendwann ein leeres
     * Passwort in der Datei, ohne dass es jemand merkt. */
    $vw_email = trim(preg_replace('/[\x00-\x1F\x7F"\']/', '',
        isset($_POST['email']) ? (string) $_POST['email'] : ''));
    $vw_pw = isset($_POST['passwort']) ? (string) $_POST['passwort'] : '';
    $vw_spin = isset($_POST['spin']) ? trim((string) $_POST['spin']) : '';
    if (isset($_POST['zugang_loeschen'])) {
        // Ausdruecklich gewollt: alles weg. Was im selben Absenden in den
        // Feldern stand, wird verworfen - sonst waere unklar, ob Loeschen
        // oder Eintragen gewonnen hat.
        if (vw_zugang_loeschen()) {
            $vw_meldungen[] = vw_t('EINST.ZUGANG_GELOESCHT');
        } else {
            $vw_fehler[] = vw_t('EINST.FEHLER_ZUGANG_LOESCHEN');
        }
    } elseif ($vw_email !== '' && !filter_var($vw_email, FILTER_VALIDATE_EMAIL)) {
        $vw_fehler[] = vw_t('EINST.FEHLER_EMAIL');
    } elseif ($vw_spin !== '' && !preg_match('/^[0-9]{4}$/', $vw_spin)) {
        // Ist die FORM eines Geheimnisses erkennbar falsch, wird beim Speichern
        // abgewiesen, statt den Benutzer in eine Fehlermeldung des Anbieters
        // laufen zu lassen.
        $vw_fehler[] = vw_t('EINST.FEHLER_SPIN');
    } else {
        if (!vw_zugang_speichern($vw_email, $vw_pw, $vw_spin)) {
            $vw_fehler[] = vw_t('EINST.FEHLER_ZUGANG_SPEICHERN');
        }
    }
    $vw_zg = vw_zugang();
    if ($vw_zg['laenge'] > 0 && $vw_zg['email'] === '') {
        $vw_fehler[] = vw_t('EINST.WARN_PW_OHNE_KONTO');
    }

    if (!$vw_fehler) {
        if (vw_config_speichern($vw_cfg)) {
            $vw_meldungen[] = vw_t('EINST.GESPEICHERT');
        } else {
            $vw_fehler[] = sprintf(vw_t('EINST.FEHLER_SPEICHERN'), $vw_p['config']);
        }
    }
    $vw_tab = 'tab-settings';

    /* mqtt_ein und mqtt_topic werden hier bewusst NICHT angefasst: sie wohnen im
     * Reiter MQTT und haben dort ein eigenes Formular. Die Konfiguration
     * kommt aus vw_config(), die Werte ueberleben also unveraendert. Stuende
     * hier weiter "isset($_POST['mqtt_ein']) ? 1 : 0", wuerde jedes Speichern
     * der Einstellungen MQTT stillschweigend abschalten. */
}

/* ---------------- MQTT (eigener Reiter, eigenes Formular) ----------------
 *
 * Eigenes Formular UND eigener Handler gehoeren zusammen. Loesten beide
 * Formulare denselben Handler aus, setzte dieser die Haken des jeweils
 * nicht abgeschickten Formulars per isset() auf 0 - der Benutzer verloere
 * Werte, die er nie gesehen hat. Der Handler laedt darum den Bestand und
 * ruehrt ausschliesslich die MQTT-Werte an. */
if ($vw_post && isset($_POST['save_mqtt'])) {
    $vw_mcfg = vw_config();
    $vw_mcfg['mqtt_ein'] = isset($_POST['mqtt_ein']) ? 1 : 0;
    $vw_mtopic = trim(preg_replace('/[\x00-\x1F\x7F"\']/', '',
        (string) (isset($_POST['mqtt_topic']) ? $_POST['mqtt_topic'] : '')));
    if ($vw_mtopic === '' || !preg_match('#^[A-Za-z0-9_/\-]{1,64}$#', $vw_mtopic)) {
        $vw_fehler[] = vw_t('EINST.FEHLER_TOPIC');
    } else {
        $vw_mcfg['mqtt_topic'] = trim($vw_mtopic, '/');
    }
    if (!$vw_fehler) {
        if (vw_config_speichern($vw_mcfg)) {
        $vw_meldungen[] = vw_t('EINST.GESPEICHERT');
        }
    }
    $vw_tab = 'tab-mqtt';
}

/* ---------------- Dienst starten, anhalten, neu starten ---------------- */
if ($vw_post && isset($_POST['dienst'])) {
    $vw_befehl = (string) $_POST['dienst'];
    list($vw_ok, $vw_ausgabe) = vw_dienst($vw_befehl);
    if ($vw_ok) {
        $vw_meldungen[] = vw_t('EINST.DIENST_' . strtoupper($vw_befehl)) . ' ' . vw_e($vw_ausgabe);
    } else {
        $vw_fehler[] = vw_e($vw_ausgabe);
    }
    $vw_tab = 'tab-settings';
}

/* ---------------- Anmeldemarken verwerfen ----------------
 * Die Bibliothek legt ihre Anmeldemarken in token.json ab und meldet sich
 * damit an, statt jedes Mal das Passwort zu senden. Sind sie verdorben -
 * etwa nach einem Passwortwechsel -, hilft nur Wegwerfen. */
if ($vw_post && isset($_POST['sitzung_verwerfen'])) {
    $vw_datei = $vw_p['datadir'] . '/token.json';
    if (is_file($vw_datei) && @unlink($vw_datei)) {
        $vw_meldungen[] = vw_t('EINST.SITZUNG_VERWORFEN');
    } else {
        $vw_meldungen[] = vw_t('EINST.SITZUNG_KEINE');
    }
    $vw_tab = 'tab-settings';
}

/* ---------------- Neues Token ---------------- */
if ($vw_post && isset($_POST['token_neu'])) {
    $vw_cfg = vw_config();
    $vw_cfg['aktionstoken'] = vw_token_erzeugen();
    if (vw_config_speichern($vw_cfg)) {
        $vw_meldungen[] = vw_t('LOX.TOKEN_NEU');
    } else {
        $vw_fehler[] = sprintf(vw_t('EINST.FEHLER_SPEICHERN'), $vw_p['config']);
    }
    $vw_tab = 'tab-loxone';
}

/* ---------------- Log leeren ---------------- */
if ($vw_post && isset($_POST['log_leeren'])) {
    @mkdir(dirname($vw_p['log']), 0775, true);
    @file_put_contents($vw_p['log'], '[' . date('Y-m-d H:i:s') . '] ' . vw_t('LOG.GELEERT') . "\n");
    $vw_meldungen[] = vw_t('LOG.GELEERT');
    $vw_tab = 'tab-log';
}

/* ---------------- Aktionen des Reiters Test ---------------- */
if ($vw_post && isset($_POST['test'])) {
    list($vw_stand, $vw_text) = vw_test_aktion((string) $_POST['test']);
    if ($vw_stand === 1) {
        $vw_meldungen[] = vw_e($vw_text);
    } else {
        $vw_fehler[] = vw_e($vw_text);
    }
    $vw_tab = 'tab-test';
}
if ($vw_post && isset($_POST['selbsttest'])) {
    $vw_testausgabe = vw_selbsttest();
    $vw_tab = 'tab-test';
}

/* ---------------- Laden ---------------- */
$vw_cfg = vw_config();
$vw_token = vw_token();
$vw_zg = vw_zugang();
$vw_fahrzeuge = vw_fahrzeuge();
$vw_zustand = vw_zustand();
$vw_alter = vw_alter();
$vw_pid = vw_dienst_pid();
$vw_mqtt = vw_mqtt_zustand();
$vw_pyv = vw_python_fassung();
$vw_libv = vw_bibliothek_fassung();
$vw_host = isset($_SERVER['HTTP_HOST']) && $_SERVER['HTTP_HOST'] !== ''
    ? preg_replace('/[^A-Za-z0-9\.\-:]/', '', (string) $_SERVER['HTTP_HOST'])
    : (gethostname() ?: 'loxberry');
$vw_basis = 'http://' . $vw_host . '/plugins/' . $vw_p['plugin'] . '/index.php';
$vw_logzeilen = is_file($vw_p['log']) ? vw_log_ende($vw_p['log'], 400) : array();

$vw_rahmen = class_exists('LBWeb', false);

/* ---------------- Einstellungen sichern ----------------
 *
 * Ausgegeben wird die VOLLE Konfiguration - samt Aktionstoken. Ohne ihn
 * stuenden nach dem Zurueckspielen alle Felder richtig, und das Plugin
 * kaeme trotzdem nicht an die Anlage; die Datei waere wertlos. Damit
 * traegt sie ein Geheimnis, und der Hinweis am Knopf sagt das. */
if ($vw_post && isset($_POST['vw_sichern'])) {
    $vw_js = json_encode(vw_config(),
        JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    if ($vw_js !== false) {
        header('Content-Type: application/json; charset=utf-8');
        header('Content-Disposition: attachment; filename="volkswagenid_einstellungen_'
               . date('Ymd_His') . '.json"');
        echo $vw_js;
        exit;
    }
    $vw_fehler[] = vw_t('EINST.SICH_SCHREIBFEHLER');
}

/* ---------------- Einstellungen zurueckspielen ----------------
 *
 * is_uploaded_file() ZUERST: ohne diese Pruefung liesse sich jede Datei des
 * Servers unterschieben. Dann die Groessengrenze - eine Sicherung dieses
 * Plugins ist wenige Kilobyte gross; alles darueber wird gar nicht gelesen. */
if ($vw_post && isset($_POST['vw_zurueck'])) {
    if (!isset($_FILES['vw_sicherung']) || !is_array($_FILES['vw_sicherung'])
        || !isset($_FILES['vw_sicherung']['tmp_name'])
        || !@is_uploaded_file($_FILES['vw_sicherung']['tmp_name'])) {
        $vw_fehler[] = vw_t('EINST.SICH_KEINE_DATEI');
    } elseif ((int) $_FILES['vw_sicherung']['size'] > 262144) {
        $vw_fehler[] = vw_t('EINST.SICH_ZU_GROSS');
    } else {
        list($vw_neu, $vw_mangel, $vw_n) = vw_sicherung_lesen(
            (string) @file_get_contents($_FILES['vw_sicherung']['tmp_name']));
        if ($vw_neu === null) {
            /* ALLE Beanstandungen, nicht nur die erste - und geaendert wird
             * nichts. */
            $vw_fehler[] = vw_t('EINST.SICH_ABGELEHNT') . ' '
                            . implode(' ', $vw_mangel);
        } elseif (vw_config_speichern($vw_neu)) {
            $vw_meldungen[] = sprintf(vw_t('EINST.SICH_UEBERNOMMEN'), $vw_n);
        } else {
            $vw_fehler[] = vw_t('EINST.SICH_SCHREIBFEHLER');
        }
    }
}


if ($vw_rahmen) {
    LBWeb::lbheader('Volkswagen ID', 'https://wiki.loxberry.de/', 'help.html');
}

?>
<style>
/* Hausstandard, wortgetreu aus VORLAGE_hausstandard.css.html uebernommen.
   Nicht neu erfinden: der Knopf-Fehler vom 30.07.2026 steckte in sieben
   Plugins gleichzeitig, weil jedes seine eigene Kopie hatte. */
.sm-wrap { max-width: 980px; margin: 0 auto; font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; color: #333; }
.sm-wrap, .sm-wrap *, .sm-tabs, .sm-tabs * { text-shadow: none !important; }
.sm-wrap h2 { color: #6dac20; margin: 24px 0 10px; font-size: 1.15em; border-bottom: 2px solid #e0e0e0; padding-bottom: 6px; }
.sm-wrap h3 { color: #4f7d17; font-size: 1.0em; font-weight: 700; margin: 16px 0 2px; }
.sm-tabs { display: flex; gap: 4px; margin: 14px 0 0; border-bottom: 2px solid #6dac20; flex-wrap: wrap; }
.sm-tab { background: #eee; border: 1px solid #ccc; border-bottom: 0; border-radius: 8px 8px 0 0;
          padding: 9px 18px; font-size: 0.95em; color: #444 !important; text-decoration: none; display: inline-block; }
.sm-tab.sm-active { background: #6dac20; color: #fff !important; border-color: #6dac20; font-weight: 600; }
.sm-feld { margin: 14px 0; }
.sm-feld > label { display: block; font-weight: 600; font-size: 0.9em; color: #555; margin: 0 0 4px; }
.sm-feld .ui-input-text, .sm-feld .ui-select, .sm-feld .ui-textinput { max-width: 520px; }
.sm-feld .ui-input-text input, .sm-feld .ui-input-text textarea { font-size: 0.95em; }
.sm-hilfe { font-size: 0.85em; color: #555; margin: 4px 0 0; max-width: 640px; }
.sm-step { border: 1px solid #ddd; border-left: 4px solid #6dac20; background: #fafafa;
    border-radius: 6px; padding: 12px 14px; margin: 12px 0; font-size: 0.92em; line-height: 1.5; }
.sm-tbl { border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 0.9em; }
.sm-tbl th, .sm-tbl td { border: 1px solid #ccc; padding: 5px 7px; text-align: left; vertical-align: top; }
.sm-tbl th { background: #eef3e6; font-weight: 600; }
.sm-mono { font-family: Consolas, "Courier New", monospace; background: #f0f0f0;
    padding: 1px 4px; border-radius: 3px; font-size: 0.94em; word-break: break-all; }
.sm-pre { background: #f4f4f4; border: 1px solid #ccc; padding: 10px; font-size: 0.85em;
    overflow: auto; margin: 8px 0; white-space: pre-wrap; }
.sm-knopfreihe { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 4px; align-items: stretch; }
.sm-knopfreihe form { margin: 0; display: flex; }
.sm-wrap .sm-knopfreihe .sm-btn, .sm-wrap a.sm-btn, .sm-wrap button.sm-btn {
    flex: 0 0 auto; min-width: 250px; text-align: center; display: inline-flex;
    align-items: center; justify-content: center; line-height: 1.25;
    padding: 10px 14px !important; border-radius: 6px !important;
    color: #fff !important; text-decoration: none !important; font-size: 0.92em;
    border: 0 !important; cursor: pointer; font-weight: 600 !important;
    text-shadow: none !important; box-shadow: none !important;
    opacity: 1 !important; margin: 0 !important; width: auto !important; }
.sm-kacheln { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0; }
.sm-kachel { border: 1px solid #ddd; border-radius: 10px; padding: 10px 14px; min-width: 130px; }
.sm-kachel b { display: block; font-size: 1.35em; color: #33691e; }
.sm-legende { display: flex; flex-wrap: wrap; gap: 14px; margin: 10px 0 2px; font-size: 0.86em; color: #555; }
.sm-legende span { display: inline-flex; align-items: center; gap: 6px; }
.sm-punkt { width: 13px; height: 13px; border-radius: 3px; display: inline-block; }
.sm-wrap .sm-btn.sm-b-lesen   { background: #6dac20 !important; }
.sm-wrap .sm-btn.sm-b-technik { background: #546e7a !important; }
.sm-wrap .sm-btn.sm-b-aktion  { background: #e0620d !important; }
.sm-wrap .sm-btn.sm-b-lesen:hover,   .sm-wrap .sm-btn.sm-b-lesen:focus   { background: #5c9219 !important; color: #fff !important; }
.sm-wrap .sm-btn.sm-b-technik:hover, .sm-wrap .sm-btn.sm-b-technik:focus { background: #435962 !important; color: #fff !important; }
.sm-wrap .sm-btn.sm-b-aktion:hover,  .sm-wrap .sm-btn.sm-b-aktion:focus  { background: #b84f0a !important; color: #fff !important; }
.sm-punkt.sm-b-lesen   { background: #6dac20; }
.sm-punkt.sm-b-technik { background: #546e7a; }
.sm-punkt.sm-b-aktion  { background: #e0620d; }
.sm-seite { display: none; padding-top: 4px; }
.sm-seite.sm-active { display: block; }
.sm-hinweis { border: 1px solid #cfe3b0; background: #f2f8ea; border-radius: 6px;
    padding: 10px 12px; margin: 12px 0; font-size: 0.9em; }
.sm-warnung { border: 1px solid #f0c9a0; background: #fdf4ec; border-radius: 6px;
    padding: 10px 12px; margin: 12px 0; font-size: 0.9em; }
.sm-fehler { border: 1px solid #ef9a9a; background: #ffebee; border-radius: 6px;
    padding: 10px 12px; margin: 12px 0; font-size: 0.9em; }
.sm-an  { color: #1a7f1a; font-weight: 700; }
.sm-aus { color: #b00000; font-weight: 700; }
.sm-log { background: #1e1e1e; color: #d4d4d4; font-family: Consolas, "Courier New", monospace;
    font-size: 0.82em; padding: 12px; border-radius: 8px; max-height: 480px; overflow: auto;
    white-space: pre-wrap; }
</style>
<div class="sm-wrap">

<?php foreach ($vw_meldungen as $vw_m) { ?>
<div class="sm-hinweis"><?= $vw_m ?></div>
<?php } ?>
<?php if ($vw_fehler) { ?>
<div class="sm-fehler"><b><?= vw_e(vw_t('ALLG.BEANSTANDUNG')) ?></b>
<ul style="margin:6px 0 0 18px;padding:0;">
<?php foreach ($vw_fehler as $vw_f) { ?><li><?= $vw_f ?></li><?php } ?>
</ul></div>
<?php } ?>

<!-- ================= Statuskacheln ================= -->
<div class="sm-kacheln">
  <div class="sm-kachel"><?= vw_e(vw_t('ALLG.DIENST')) ?>
    <b class="<?= $vw_pid ? 'sm-an' : 'sm-aus' ?>"><?= $vw_pid ? vw_e(vw_t('ALLG.LAEUFT')) : vw_e(vw_t('ALLG.GESTOPPT')) ?></b>
    <span class="sm-hilfe"><?= $vw_pid ? 'PID ' . (int) $vw_pid : vw_e(vw_t('ALLG.KEINE_PID')) ?></span>
  </div>
  <div class="sm-kachel"><?= vw_e(vw_t('ALLG.LETZTER_ABRUF')) ?>
    <b><?= $vw_alter < 0 ? '&ndash;' : (int) $vw_alter . ' s' ?></b>
    <span class="sm-hilfe"><?= $vw_alter < 0 ? vw_e(vw_t('ALLG.NIE')) : vw_e(date('d.m.Y H:i:s', time() - $vw_alter)) ?></span>
  </div>
  <div class="sm-kachel"><?= vw_e(vw_t('ALLG.FAHRZEUGE')) ?>
    <b><?= count($vw_fahrzeuge) ?></b>
    <span class="sm-hilfe"><?= $vw_libv !== '' ? vw_e($vw_libv) : vw_e(vw_t('ALLG.LIB_FEHLT')) ?></span>
  </div>
  <div class="sm-kachel">MQTT
    <b class="<?= $vw_mqtt['autostart'] ? 'sm-an' : 'sm-aus' ?>"><?= $vw_mqtt['autostart'] ? vw_e(vw_t('ALLG.EIN')) : vw_e(vw_t('ALLG.AUS')) ?></b>
    <span class="sm-hilfe"><?= vw_e(vw_t('ALLG.GATEWAY')) ?></span>
  </div>
</div>

<?php if (!empty($vw_zustand['fehler'])) { ?>
<div class="sm-warnung"><b><?= vw_e(vw_t('ALLG.LETZTE_STOERUNG')) ?></b> <?= vw_e($vw_zustand['fehler']) ?></div>
<?php } ?>

<?php foreach ($vw_fahrzeuge as $vw_nr => $vw_fz) { ?>
<div class="sm-hinweis">
<b><?= vw_e($vw_fz['modell'] ? $vw_fz['modell'] : vw_t('ALLG.OHNE_NAMEN')) ?></b>
(<?= vw_e(vw_t('ALLG.FAHRZEUG')) ?> <?= vw_e($vw_nr) ?><?= !empty($vw_fz['kennzeichen']) ? ', ' . vw_e($vw_fz['kennzeichen']) : '' ?>)
&middot; <?= vw_e(vw_t('ALLG.SOC')) ?> <b><?= !isset($vw_fz['soc']) || $vw_fz['soc'] === null ? '&ndash;' : vw_e($vw_fz['soc']) . ' %' ?></b>
&middot; <?= vw_e(vw_t('ALLG.REICHWEITE')) ?> <?= !isset($vw_fz['reichweite_km']) || $vw_fz['reichweite_km'] === null ? '&ndash;' : vw_e($vw_fz['reichweite_km']) . ' km' ?>
&middot; <?= vw_e(vw_t('ALLG.KM')) ?> <?= !isset($vw_fz['kilometerstand']) || $vw_fz['kilometerstand'] === null ? '&ndash;' : vw_e($vw_fz['kilometerstand']) . ' km' ?>
&middot; <?= vw_e(vw_t('ALLG.VERRIEGELT')) ?>
<?php if (!isset($vw_fz['verriegelt']) || $vw_fz['verriegelt'] === null) { ?>&ndash;<?php
      } elseif ($vw_fz['verriegelt']) { ?><span class="sm-an"><?= vw_e(vw_t('ALLG.JA')) ?></span><?php
      } else { ?><span class="sm-aus"><?= vw_e(vw_t('ALLG.NEIN')) ?></span><?php } ?>
<div style="margin-top:8px;"><?= vw_soc_svg(vw_verlauf_lesen((int) $vw_nr)) ?></div>
<div class="sm-hilfe"><?= vw_e(vw_t('ALLG.VERLAUF_HINWEIS')) ?></div>
<?php if (!empty($vw_fz['ausfaelle']) && is_array($vw_fz['ausfaelle'])) { ?>
<div class="sm-hilfe"><b><?= vw_e(vw_t('ALLG.AUSFAELLE')) ?></b>
<?php foreach ($vw_fz['ausfaelle'] as $vw_ep => $vw_gr) { ?>
<br><span class="sm-mono"><?= vw_e($vw_ep) ?></span>: <?= vw_e($vw_gr) ?>
<?php } ?>
</div>
<?php } ?>
</div>
<?php } ?>

<!-- Reiterleiste: echte Links, JavaScript faengt den Klick ab. So bleibt jeder
     Reiter verlinkbar, Eingaben in anderen Reitern gehen nicht verloren, und
     faellt das Skript aus, ist die Seite weiterhin bedienbar. -->
<?php
$vw_beschriftung = array(
    'settings' => 'REITER.EINSTELLUNGEN', 'mqtt' => '', 'loxone' => 'REITER.LOXONE',
    'test'     => 'REITER.TEST',          'log'  => 'REITER.LOG',
);
?>
<div class="sm-tabs">
<?php foreach ($vw_reiter_ids as $vw_r) {
    $vw_bez = $vw_beschriftung[$vw_r] !== '' ? vw_t($vw_beschriftung[$vw_r]) : 'MQTT'; ?>
	<a class="sm-tab<?= $vw_tab === 'tab-' . $vw_r ? ' sm-active' : '' ?>" data-ziel="tab-<?= $vw_r ?>" href="index.php?form=<?= $vw_r ?>"><?= vw_e($vw_bez) ?></a>
<?php } ?>
</div>

<!-- ================= Reiter: Einstellungen ================= -->
<div class="sm-seite<?= $vw_tab === 'tab-settings' ? ' sm-active' : '' ?>" id="tab-settings">

<?php if ($vw_pyv !== '' && version_compare($vw_pyv, '3.9.0', '<')) { ?>
<div class="sm-fehler"><?= vw_t('EINST.PYTHON_ZU_ALT') ?></div>
<?php } ?>

<h2><?= vw_e(vw_t('EINST.H_DIENST')) ?></h2>
<p class="sm-hilfe"><?= vw_t('EINST.DIENST_ERKLAERUNG') ?></p>
<div class="sm-legende">
<span><i class="sm-punkt sm-b-lesen"></i> <?= vw_t('LEGENDE.LESEN') ?></span>
<span><i class="sm-punkt sm-b-aktion"></i> <?= vw_t('LEGENDE.AKTION') ?></span>
</div>
<div class="sm-knopfreihe">
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-settings">
    <button data-role="none" class="sm-btn sm-b-lesen" type="submit" name="dienst" value="start"><?= vw_e(vw_t('EINST.K_START')) ?></button>
  </form>
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-settings">
    <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="dienst" value="restart"><?= vw_e(vw_t('EINST.K_NEUSTART')) ?></button>
  </form>
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-settings">
    <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="dienst" value="stop"><?= vw_e(vw_t('EINST.K_STOPP')) ?></button>
  </form>
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-settings">
    <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="sitzung_verwerfen" value="1"><?= vw_e(vw_t('EINST.K_SITZUNG')) ?></button>
  </form>
</div>

<form action="index.php" method="post" autocomplete="off">
<input data-role="none" type="hidden" name="speichern" value="1">
<input data-role="none" type="hidden" name="activetab" value="tab-settings">

<h2><?= vw_e(vw_t('EINST.H_KONTO')) ?></h2>
<div class="sm-warnung"><?= vw_t('EINST.KONTO_ERKLAERUNG') ?></div>
<div class="sm-feld">
  <label for="email"><?= vw_e(vw_t('EINST.L_EMAIL')) ?></label>
  <input data-role="none" type="text" id="email" name="email" value="<?= vw_e($vw_zg['email']) ?>" placeholder="name@example.com">
  <div class="sm-hilfe"><?= vw_t('EINST.H_EMAIL') ?></div>
</div>
<div class="sm-feld">
  <label for="passwort"><?= vw_e(vw_t('EINST.L_PASSWORT')) ?></label>
  <input data-role="none" type="password" id="passwort" name="passwort" value="" placeholder="<?= $vw_zg['laenge'] > 0 ? vw_e(sprintf(vw_t('EINST.PW_GESETZT'), $vw_zg['laenge'])) : vw_e(vw_t('EINST.PW_LEER')) ?>">
  <div class="sm-hilfe"><?= vw_t('EINST.H_PASSWORT') ?></div>
</div>
<div class="sm-feld">
  <label for="spin"><?= vw_e(vw_t('EINST.L_SPIN')) ?></label>
  <input data-role="none" type="password" id="spin" name="spin" value="" maxlength="4" placeholder="<?= $vw_zg['spin_laenge'] > 0 ? vw_e(vw_t('EINST.SPIN_GESETZT')) : vw_e(vw_t('EINST.SPIN_LEER')) ?>">
  <div class="sm-hilfe"><?= vw_t('EINST.H_SPIN') ?></div>
</div>
<div class="sm-hinweis"><?= vw_t('EINST.SITZUNG_ERKLAERUNG') ?></div>
<?php if ($vw_zg['email'] !== '' || $vw_zg['laenge'] > 0 || $vw_zg['spin_laenge'] > 0) { ?>
<div class="sm-feld">
  <label style="display:inline-flex;align-items:center;gap:8px;">
    <input data-role="none" type="checkbox" name="zugang_loeschen" value="1">
    <?= vw_e(vw_t('EINST.L_ZUGANG_LOESCHEN')) ?>
  </label>
  <div class="sm-hilfe"><?= vw_t('EINST.H_ZUGANG_LOESCHEN') ?></div>
</div>
<?php } ?>

<h2><?= vw_e(vw_t('EINST.H_TAKT')) ?></h2>
<div class="sm-warnung"><?= vw_t('EINST.TAKT_WARNUNG') ?></div>
<div class="sm-feld">
  <label for="intervall"><?= vw_e(vw_t('EINST.L_INTERVALL')) ?></label>
  <input data-role="none" type="number" id="intervall" name="intervall" value="<?= (int) $vw_cfg['intervall'] ?>" min="180" max="3600">
  <div class="sm-hilfe"><?= vw_t('EINST.H_INTERVALL') ?></div>
</div>
<div class="sm-feld">
  <label for="takt_wartung"><?= vw_e(vw_t('EINST.L_TAKT_WARTUNG')) ?></label>
  <input data-role="none" type="number" id="takt_wartung" name="takt_wartung" value="<?= (int) $vw_cfg['takt_wartung'] ?>" min="1" max="240">
  <div class="sm-hilfe"><?= vw_t('EINST.H_TAKT_WARTUNG') ?></div>
</div>
<div class="sm-feld">
  <label for="verlauf_tage"><?= vw_e(vw_t('EINST.L_VERLAUF_TAGE')) ?></label>
  <input data-role="none" type="number" id="verlauf_tage" name="verlauf_tage" value="<?= (int) $vw_cfg['verlauf_tage'] ?>" min="1" max="90">
  <div class="sm-hilfe"><?= vw_t('EINST.H_VERLAUF_TAGE') ?></div>
</div>

<h2><?= vw_e(vw_t('EINST.H_STEUERUNG')) ?></h2>
<div class="sm-warnung"><?= vw_t('EINST.STEUERUNG_ERKLAERUNG') ?></div>
<div class="sm-feld">
  <label style="display:inline-flex;align-items:center;gap:8px;">
    <input data-role="none" type="checkbox" name="steuerung_ein" value="1" <?= !empty($vw_cfg['steuerung_ein']) ? 'checked' : '' ?>>
    <?= vw_e(vw_t('EINST.L_STEUERUNG_EIN')) ?>
  </label>
</div>
<div class="sm-feld">
  <label for="temp_min"><?= vw_e(vw_t('EINST.L_TEMP_MIN')) ?></label>
  <input data-role="none" type="number" id="temp_min" name="temp_min" value="<?= (int) $vw_cfg['temp_min'] ?>" min="10" max="30">
</div>
<div class="sm-feld">
  <label for="temp_max"><?= vw_e(vw_t('EINST.L_TEMP_MAX')) ?></label>
  <input data-role="none" type="number" id="temp_max" name="temp_max" value="<?= (int) $vw_cfg['temp_max'] ?>" min="10" max="30">
  <div class="sm-hilfe"><?= vw_t('EINST.H_TEMP') ?></div>
</div>
<div class="sm-feld">
  <label style="display:inline-flex;align-items:center;gap:8px;">
    <input data-role="none" type="checkbox" name="zugriff_erzwingen" value="1" <?= !empty($vw_cfg['zugriff_erzwingen']) ? 'checked' : '' ?>>
    <?= vw_e(vw_t('EINST.L_ZUGRIFF_ERZWINGEN')) ?>
  </label>
  <div class="sm-hilfe"><?= vw_t('EINST.H_ZUGRIFF_ERZWINGEN') ?></div>
</div>
<div class="sm-feld">
  <label for="wartezeit"><?= vw_e(vw_t('EINST.L_WARTEZEIT')) ?></label>
  <input data-role="none" type="number" id="wartezeit" name="wartezeit" value="<?= (int) $vw_cfg['wartezeit'] ?>" min="0" max="30">
  <div class="sm-hilfe"><?= vw_t('EINST.H_WARTEZEIT') ?></div>
</div>

<?php /* MQTT stand hier bis zu dieser Fassung. Es wohnt jetzt
         vollstaendig im Reiter MQTT - eine Sache, eine Stelle. */ ?>

<div class="sm-knopfreihe">
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit"><?= vw_e(vw_t('ALLG.SPEICHERN')) ?></button>
</div>
</form>

<h2><?= vw_e(vw_t('EINST.H_ERKANNT')) ?></h2>
<?php if (!$vw_fahrzeuge) { ?>
<div class="sm-warnung"><?= vw_t('EINST.KEINE_FAHRZEUGE') ?></div>
<?php } else { ?>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('EINST.T_NR')) ?></th><th><?= vw_e(vw_t('EINST.T_MODELL')) ?></th>
    <th><?= vw_e(vw_t('EINST.T_KENNZEICHEN')) ?></th><th><?= vw_e(vw_t('EINST.T_VIN')) ?></th>
    <th><?= vw_e(vw_t('EINST.T_ANTRIEB')) ?></th><th><?= vw_e(vw_t('EINST.T_BATTERIE')) ?></th>
    <th><?= vw_e(vw_t('EINST.T_SOFTWARE')) ?></th></tr>
<?php foreach ($vw_fahrzeuge as $vw_nr => $vw_fz) { ?>
<tr><td><?= vw_e($vw_nr) ?></td><td><?= vw_e($vw_fz['modell']) ?></td>
    <td><?= vw_e($vw_fz['kennzeichen']) ?></td>
    <td><span class="sm-mono"><?= vw_e($vw_fz['vin']) ?></span></td>
    <td><?= vw_e($vw_fz['antriebsart']) ?></td>
    <td><?= empty($vw_fz['batterie_kwh']) ? '&mdash;' : vw_e($vw_fz['batterie_kwh']) . ' kWh' ?></td>
    <td><?= vw_e($vw_fz['software']) ?></td></tr>
<?php } ?>
</table>
<p class="sm-hilfe"><?= vw_t('EINST.VIN_HINWEIS') ?></p>
<?php } ?>

<h2><?= vw_t('EINST.H_SICHERUNG') ?></h2>
<div class="sm-hinweis"><?= vw_t('EINST.SICH_ERKLAERUNG') ?></div>
<div class="sm-warnung"><?= vw_t('EINST.SICH_WARNUNG') ?></div>
<div class="sm-knopfreihe">
  <!-- ZWEI GETRENNTE Formulare. Das Sichern schickt einen Download und ruft
       exit auf; das Zurueckspielen braucht enctype="multipart/form-data".
       Wer beides in ein Formular legt, bekommt entweder keinen Upload oder
       einen Download, der das Speichern verschluckt. -->
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-settings">
    <button data-role="none" class="sm-btn sm-b-lesen" type="submit" name="vw_sichern" value="1"><?= vw_t('EINST.K_SICHERN') ?></button>
  </form>
  <form action="index.php" method="post" enctype="multipart/form-data">
    <input data-role="none" type="hidden" name="activetab" value="tab-settings">
    <input data-role="none" type="file" name="vw_sicherung" accept=".json">
    <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="vw_zurueck" value="1"><?= vw_t('EINST.K_ZURUECK') ?></button>
  </form>
</div>
</div>

<!-- ================= Reiter: MQTT ================= -->
<div class="sm-seite<?= $vw_tab === 'tab-mqtt' ? ' sm-active' : '' ?>" id="tab-mqtt">

<h2>MQTT</h2>
<form action="index.php" method="post">
<input data-role="none" type="hidden" name="save_mqtt" value="1">
<input data-role="none" type="hidden" name="activetab" value="tab-mqtt">
<div class="sm-feld">
  <label style="display:inline-flex;align-items:center;gap:8px;">
    <input data-role="none" type="checkbox" name="mqtt_ein" value="1" <?= !empty($vw_cfg['mqtt_ein']) ? 'checked' : '' ?>>
    <?= vw_e(vw_t('EINST.L_MQTT_EIN')) ?>
  </label>
</div>
<div class="sm-feld">
  <label for="mqtt_topic"><?= vw_e(vw_t('EINST.L_MQTT_TOPIC')) ?></label>
  <input data-role="none" type="text" id="mqtt_topic" name="mqtt_topic" value="<?= vw_e($vw_cfg['mqtt_topic']) ?>" placeholder="vw">
  <div class="sm-hilfe"><?= vw_t('EINST.H_MQTT_TOPIC') ?></div>
</div>
<div class="sm-legende"><span><i class="sm-punkt sm-b-aktion"></i> <?= vw_t('LEGENDE.AKTION') ?></span></div>
<div class="sm-knopfreihe">
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit"><?= vw_e(vw_t('ALLG.SPEICHERN')) ?></button>
</div>
</form>
<h2><?= vw_e(vw_t('MQTT.H_ZUSTAND')) ?></h2>
<p class="sm-hilfe"><?= vw_t('MQTT.GATEWAY_ERKLAERUNG') ?></p>

<?php if (!$vw_mqtt['gefunden']) { ?>
<div class="sm-fehler"><?= vw_t('MQTT.NICHT_GEFUNDEN') ?></div>
<?php } elseif (!$vw_mqtt['autostart']) { ?>
<div class="sm-fehler"><?= vw_t('MQTT.AUTOSTART_AUS') ?></div>
<?php } else { ?>
<div class="sm-hinweis"><?= vw_t('MQTT.AUTOSTART_EIN') ?></div>
<?php } ?>

<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('ALLG.EIGENSCHAFT')) ?></th><th><?= vw_e(vw_t('ALLG.WERT')) ?></th></tr>
<tr><td><?= vw_e(vw_t('MQTT.T_AUTOSTART')) ?></td><td class="<?= $vw_mqtt['autostart'] ? 'sm-an' : 'sm-aus' ?>"><?= $vw_mqtt['autostart'] ? vw_e(vw_t('ALLG.EIN')) : vw_e(vw_t('ALLG.AUS')) ?></td></tr>
<tr><td><?= vw_e(vw_t('MQTT.T_BROKER')) ?></td><td><span class="sm-mono"><?= vw_e($vw_mqtt['broker']) ?>:<?= vw_e($vw_mqtt['brokerport']) ?></span></td></tr>
<tr><td><?= vw_e(vw_t('MQTT.T_UDP')) ?></td><td><span class="sm-mono"><?= (int) $vw_mqtt['udpport'] ?></span></td></tr>
<tr><td><?= vw_e(vw_t('MQTT.T_PLUGIN')) ?></td><td class="<?= !empty($vw_cfg['mqtt_ein']) ? 'sm-an' : 'sm-aus' ?>"><?= !empty($vw_cfg['mqtt_ein']) ? vw_e(vw_t('ALLG.EIN')) : vw_e(vw_t('ALLG.AUS')) ?></td></tr>
</table>

<h2><?= vw_e(vw_t('MQTT.H_ABO')) ?></h2>
<div class="sm-warnung"><?= vw_abo_text() ?></div>
<div class="sm-step">
<?= vw_t('MQTT.ABO_SCHRITTE') ?>
<p><span class="sm-mono"><?= vw_e($vw_cfg['mqtt_topic']) ?>/#</span></p>
</div>

<h2><?= vw_e(vw_t('MQTT.H_THEMEN')) ?></h2>
<p class="sm-hilfe"><?= vw_t('MQTT.THEMEN_ERKLAERUNG') ?></p>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('MQTT.T_THEMA')) ?></th><th><?= vw_e(vw_t('MQTT.T_BEDEUTUNG')) ?></th></tr>
<?php foreach (vw_mqtt_themen() as $vw_thema => $vw_schluessel) { ?>
<tr><td><span class="sm-mono"><?= vw_e($vw_cfg['mqtt_topic'] . '/' . $vw_thema) ?></span></td>
    <td><?= vw_t($vw_schluessel) ?></td></tr>
<?php } ?>
</table>
<p class="sm-hilfe"><?= vw_t('MQTT.PLATZHALTER') ?></p>
</div>

<!-- ================= Reiter: Einbindung in Loxone ================= -->
<div class="sm-seite<?= $vw_tab === 'tab-loxone' ? ' sm-active' : '' ?>" id="tab-loxone">
<h2><?= vw_e(vw_t('LOX.H_TITEL')) ?></h2>
<p><?= vw_t('LOX.EINLEITUNG') ?></p>

<div class="sm-step"><b><?= vw_e(vw_t('LOX.S1_TITEL')) ?></b><br>
<?= vw_t('LOX.S1_TEXT') ?>
</div>

<div class="sm-step"><b><?= vw_e(vw_t('LOX.S2_TITEL')) ?></b><br>
<?= vw_t('LOX.S2_TEXT') ?>
<p><span class="sm-mono"><?= vw_e($vw_cfg['mqtt_topic']) ?>/#</span></p>
<div class="sm-warnung"><?= vw_abo_text() ?></div>
</div>

<div class="sm-step"><b><?= vw_e(vw_t('LOX.S3_TITEL')) ?></b><br>
<?= vw_t('LOX.S3_TEXT') ?>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('ALLG.EIGENSCHAFT')) ?></th><th><?= vw_e(vw_t('ALLG.WERT')) ?></th></tr>
<tr><td><?= vw_e(vw_t('LOX.T_ADRESSE')) ?></td>
    <td><span class="sm-mono"><?= vw_e($vw_basis) ?>?token=<?= vw_e($vw_token) ?>&amp;aktion=status&amp;fahrzeug=1</span></td></tr>
<tr><td><?= vw_e(vw_t('LOX.T_ZYKLUS')) ?></td><td>300 <?= vw_e(vw_t('ALLG.SEKUNDEN')) ?></td></tr>
</table>
<?= vw_t('LOX.S3_BEFEHLE') ?>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('LOX.T_TITEL')) ?></th><th><?= vw_e(vw_t('LOX.T_BEFEHL')) ?></th>
    <th><?= vw_e(vw_t('LOX.T_EINHEIT')) ?></th><th><?= vw_e(vw_t('LOX.T_BEDEUTUNG')) ?></th></tr>
<?php foreach (vw_status_felder() as $vw_feld => $vw_info) { ?>
<tr><td><span class="sm-mono">VW_1_<?= vw_e($vw_feld) ?></span></td>
    <td><span class="sm-mono"><?= vw_e(vw_check($vw_feld)) ?></span></td>
    <td><?= $vw_info[0] ?></td><td><?= vw_t($vw_info[1]) ?></td></tr>
<?php } ?>
</table>
<div class="sm-warnung"><?= vw_t('LOX.S3_STRICH') ?></div>
<?php if (count($vw_fahrzeuge) > 1) { ?>
<p><b><?= vw_e(vw_t('LOX.MEHRERE_FAHRZEUGE')) ?></b></p>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('ALLG.FAHRZEUG')) ?></th><th><?= vw_e(vw_t('EINST.T_MODELL')) ?></th><th><?= vw_e(vw_t('LOX.T_ADRESSE')) ?></th></tr>
<?php foreach ($vw_fahrzeuge as $vw_nr => $vw_fz) { ?>
<tr><td><?= vw_e($vw_nr) ?></td><td><?= vw_e($vw_fz['modell']) ?></td>
    <td><span class="sm-mono"><?= vw_e($vw_basis) ?>?token=<?= vw_e($vw_token) ?>&amp;aktion=status&amp;fahrzeug=<?= vw_e($vw_nr) ?></span></td></tr>
<?php } ?>
</table>
<?php } ?>
<div class="sm-knopfreihe">
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-loxone">
    <input data-role="none" type="hidden" name="vorlage" value="1">
    <button data-role="none" class="sm-btn sm-b-lesen" type="submit"><?= vw_e(vw_t('LOX.K_VORLAGE')) ?></button>
  </form>
</div>
<div class="sm-legende">
<span><i class="sm-punkt sm-b-lesen"></i> <?= vw_t('LEGENDE.LESEN') ?></span>
</div>
</div>

<div class="sm-step"><b><?= vw_e(vw_t('LOX.S4_TITEL')) ?></b><br>
<?= vw_t('LOX.S4_TEXT') ?>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('ALLG.EIGENSCHAFT')) ?></th><th><?= vw_e(vw_t('ALLG.WERT')) ?></th></tr>
<tr><td><?= vw_e(vw_t('LOX.T_ADRESSE')) ?></td><td><span class="sm-mono"><?= vw_e($vw_basis) ?>?token=<?= vw_e($vw_token) ?>&amp;aktion=laden&amp;fahrzeug=1</span></td></tr>
<tr><td><?= vw_e(vw_t('LOX.T_ZYKLUS')) ?></td><td>300 <?= vw_e(vw_t('ALLG.SEKUNDEN')) ?></td></tr>
</table>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('LOX.T_BEFEHL')) ?></th><th><?= vw_e(vw_t('LOX.T_EINHEIT')) ?></th><th><?= vw_e(vw_t('LOX.T_BEDEUTUNG')) ?></th></tr>
<?php foreach (vw_laden_felder() as $vw_feld => $vw_info) { ?>
<tr><td><span class="sm-mono"><?= vw_e(vw_check($vw_feld)) ?></span></td>
    <td><?= $vw_info[0] ?></td><td><?= vw_t($vw_info[1]) ?></td></tr>
<?php } ?>
</table>
<?= vw_t('LOX.S4_WARTUNG') ?>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('LOX.T_BEFEHL')) ?></th><th><?= vw_e(vw_t('LOX.T_EINHEIT')) ?></th><th><?= vw_e(vw_t('LOX.T_BEDEUTUNG')) ?></th></tr>
<?php foreach (vw_wartung_felder() as $vw_feld => $vw_info) { ?>
<tr><td><span class="sm-mono"><?= vw_e(vw_check($vw_feld)) ?></span></td>
    <td><?= $vw_info[0] ?></td><td><?= vw_t($vw_info[1]) ?></td></tr>
<?php } ?>
</table>
<?= vw_t('LOX.S4_POSITION') ?>
<table class="sm-tbl">
<tr><td><span class="sm-mono"><?= vw_e($vw_basis) ?>?token=<?= vw_e($vw_token) ?>&amp;aktion=position&amp;fahrzeug=1</span></td>
    <td><span class="sm-mono"><?= vw_e(vw_check('BREITE')) ?></span> / <span class="sm-mono"><?= vw_e(vw_check('LAENGE')) ?></span></td></tr>
</table>
</div>

<div class="sm-step"><b><?= vw_e(vw_t('LOX.S5_TITEL')) ?></b><br>
<?= vw_t('LOX.S5_TEXT') ?>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('ALLG.EIGENSCHAFT')) ?></th><th><?= vw_e(vw_t('ALLG.WERT')) ?></th></tr>
<tr><td><?= vw_e(vw_t('LOX.T_VA_ADRESSE')) ?></td><td><span class="sm-mono">http://<?= vw_e($vw_host) ?></span></td></tr>
<tr><td><?= vw_e(vw_t('LOX.T_VA_KLIMA_EIN')) ?></td>
    <td><span class="sm-mono">/plugins/<?= vw_e($vw_p['plugin']) ?>/index.php?token=<?= vw_e($vw_token) ?>&amp;aktion=klima_start&amp;fahrzeug=1&amp;temp=21</span></td></tr>
<tr><td><?= vw_e(vw_t('LOX.T_VA_KLIMA_AUS')) ?></td>
    <td><span class="sm-mono">/plugins/<?= vw_e($vw_p['plugin']) ?>/index.php?token=<?= vw_e($vw_token) ?>&amp;aktion=klima_stop&amp;fahrzeug=1</span></td></tr>
<tr><td><?= vw_e(vw_t('LOX.T_VA_LADEN_EIN')) ?></td>
    <td><span class="sm-mono">/plugins/<?= vw_e($vw_p['plugin']) ?>/index.php?token=<?= vw_e($vw_token) ?>&amp;aktion=laden_start&amp;fahrzeug=1</span></td></tr>
<tr><td><?= vw_e(vw_t('LOX.T_VA_LADEN_AUS')) ?></td>
    <td><span class="sm-mono">/plugins/<?= vw_e($vw_p['plugin']) ?>/index.php?token=<?= vw_e($vw_token) ?>&amp;aktion=laden_stop&amp;fahrzeug=1</span></td></tr>
<tr><td><?= vw_e(vw_t('LOX.T_VA_LADEGRENZE')) ?></td>
    <td><span class="sm-mono">/plugins/<?= vw_e($vw_p['plugin']) ?>/index.php?token=<?= vw_e($vw_token) ?>&amp;aktion=ladegrenze&amp;fahrzeug=1&amp;prozent=&lt;v&gt;</span></td></tr>
<tr><td><?= vw_e(vw_t('LOX.T_VA_LADESTROM')) ?></td>
    <td><span class="sm-mono">/plugins/<?= vw_e($vw_p['plugin']) ?>/index.php?token=<?= vw_e($vw_token) ?>&amp;aktion=ladestrom&amp;fahrzeug=1&amp;ampere=&lt;v&gt;</span></td></tr>
<tr><td><?= vw_e(vw_t('LOX.T_VA_SCHEIBE')) ?></td>
    <td><span class="sm-mono">/plugins/<?= vw_e($vw_p['plugin']) ?>/index.php?token=<?= vw_e($vw_token) ?>&amp;aktion=scheibe_ein&amp;fahrzeug=1</span></td></tr>
<tr><td><?= vw_e(vw_t('LOX.T_VA_ABRUF')) ?></td>
    <td><span class="sm-mono">/plugins/<?= vw_e($vw_p['plugin']) ?>/index.php?token=<?= vw_e($vw_token) ?>&amp;aktion=abruf</span></td></tr>
</table>
<div class="sm-warnung"><?= vw_t('LOX.S5_WARNUNG') ?></div>
</div>

<div class="sm-step"><b><?= vw_e(vw_t('LOX.S6_TITEL')) ?></b><br>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('ALLG.EIGENSCHAFT')) ?></th><th><?= vw_e(vw_t('ALLG.WERT')) ?></th></tr>
<tr><td><?= vw_e(vw_t('LOX.T_TOKEN')) ?></td><td><span class="sm-mono"><?= vw_e($vw_token) ?></span></td></tr>
</table>
<?= vw_t('LOX.S6_TEXT') ?>
<div class="sm-knopfreihe">
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-loxone">
    <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="token_neu" value="1"><?= vw_e(vw_t('LOX.K_TOKEN_NEU')) ?></button>
  </form>
</div>
<div class="sm-legende">
<span><i class="sm-punkt sm-b-aktion"></i> <?= vw_t('LEGENDE.AKTION_TOKEN') ?></span>
</div>
</div>

<div class="sm-step"><b><?= vw_e(vw_t('LOX.S7_TITEL')) ?></b><br>
<?= vw_t('LOX.S7_TEXT') ?>
</div>

<?php
/**
 * Die komplette Baustein-Liste. Pflicht im Hausstandard.
 *
 * Anspruch: Wer die Tabelle von oben nach unten abarbeitet, hat die Funktion
 * nachgebaut, ohne nachzudenken. Loxone Config fuehrt alle Bausteine in der
 * Baustein-Suche (F5).
 *
 * Je Zeile: Nummer, Typ, Name, Parameter, woran die Eingaenge kommen.
 * Typ, Name und Parameter stehen als Sprachschluessel drin, die Eingangsspalte
 * ist symbolisch und damit sprachfrei.
 */
function vw_bausteine()
{
    return array(
        array(1,  'BAUSTEIN.T_VE',      'BAUSTEIN.N01', 'BAUSTEIN.P01', '&mdash;'),
        array(2,  'BAUSTEIN.T_VE',      'BAUSTEIN.N02', 'BAUSTEIN.P02', '&mdash;'),
        array(3,  'BAUSTEIN.T_VE',      'BAUSTEIN.N03', 'BAUSTEIN.P03', '&mdash;'),
        array(4,  'BAUSTEIN.T_VE',      'BAUSTEIN.N04', 'BAUSTEIN.P04', '&mdash;'),
        array(5,  'BAUSTEIN.T_VE',      'BAUSTEIN.N05', 'BAUSTEIN.P05', '&mdash;'),
        array(6,  'BAUSTEIN.T_VE',      'BAUSTEIN.N06', 'BAUSTEIN.P06', '&mdash;'),
        array(7,  'BAUSTEIN.T_VE',      'BAUSTEIN.N07', 'BAUSTEIN.P07', '&mdash;'),
        array(8,  'BAUSTEIN.T_VE',      'BAUSTEIN.N08', 'BAUSTEIN.P08', '&mdash;'),
        array(9,  'BAUSTEIN.T_VE',      'BAUSTEIN.N09', 'BAUSTEIN.P09', '&mdash;'),
        array(10, 'BAUSTEIN.T_VE',      'BAUSTEIN.N10', 'BAUSTEIN.P10', '&mdash;'),
        array(11, 'BAUSTEIN.T_VE',      'BAUSTEIN.N11', 'BAUSTEIN.P11', '&mdash;'),
        array(12, 'BAUSTEIN.T_VE',      'BAUSTEIN.N12', 'BAUSTEIN.P12', '&mdash;'),
        array(13, 'BAUSTEIN.T_NICHT',   'BAUSTEIN.N13', '',             'I &larr; #5'),
        array(14, 'BAUSTEIN.T_ODER',    'BAUSTEIN.N14', '',             'I1 &larr; #13, #6 &middot; I2 &larr; #7, #8'),
        array(15, 'BAUSTEIN.T_EVZ',     'BAUSTEIN.N15', 'BAUSTEIN.P15', 'I &larr; #14'),
        array(16, 'BAUSTEIN.T_BENACHR', 'BAUSTEIN.N16', 'BAUSTEIN.P16', 'I &larr; #15'),
        array(17, 'BAUSTEIN.T_SWS',     'BAUSTEIN.N17', 'BAUSTEIN.P17', 'I &larr; #1'),
        array(18, 'BAUSTEIN.T_SWS',     'BAUSTEIN.N18', 'BAUSTEIN.P18', 'I &larr; #2'),
        array(19, 'BAUSTEIN.T_ODER',    'BAUSTEIN.N19', '',             'I1 &larr; #17, I2 &larr; #18'),
        array(20, 'BAUSTEIN.T_BENACHR', 'BAUSTEIN.N20', 'BAUSTEIN.P20', 'I &larr; #19'),
        array(21, 'BAUSTEIN.T_SWS',     'BAUSTEIN.N21', 'BAUSTEIN.P21', 'I &larr; #10'),
        array(22, 'BAUSTEIN.T_BENACHR', 'BAUSTEIN.N22', 'BAUSTEIN.P22', 'I &larr; #21'),
        array(23, 'BAUSTEIN.T_SWS',     'BAUSTEIN.N23', 'BAUSTEIN.P23', 'I &larr; #12'),
        array(24, 'BAUSTEIN.T_BENACHR', 'BAUSTEIN.N24', 'BAUSTEIN.P24', 'I &larr; #23'),
        array(25, 'BAUSTEIN.T_SWS',     'BAUSTEIN.N25', 'BAUSTEIN.P25', 'I &larr; #11'),
        array(26, 'BAUSTEIN.T_BENACHR', 'BAUSTEIN.N26', 'BAUSTEIN.P26', 'I &larr; #25'),
        array(27, 'BAUSTEIN.T_STATUS',  'BAUSTEIN.N27', 'BAUSTEIN.P27', 'I1 &larr; #1, I2 &larr; #3, I3 &larr; #5'),
        array(28, 'BAUSTEIN.T_WOCHE',   'BAUSTEIN.N28', 'BAUSTEIN.P28', '&mdash;'),
        array(29, 'BAUSTEIN.T_TASTER',  'BAUSTEIN.N29', 'BAUSTEIN.P29', '&mdash;'),
        array(30, 'BAUSTEIN.T_UND',     'BAUSTEIN.N30', 'BAUSTEIN.P30', 'I1 &larr; #28, I2 &larr; ' . vw_t('BAUSTEIN.ANWESEND')),
        array(31, 'BAUSTEIN.T_ODER',    'BAUSTEIN.N31', '',             'I1 &larr; #29, I2 &larr; #30'),
        array(32, 'BAUSTEIN.T_IMPULS',  'BAUSTEIN.N32', 'BAUSTEIN.P32', 'I &larr; #31'),
        array(33, 'BAUSTEIN.T_VA',      'BAUSTEIN.N33', 'BAUSTEIN.P33', 'I &larr; #32'),
        array(34, 'BAUSTEIN.T_VA',      'BAUSTEIN.N34', 'BAUSTEIN.P34', vw_t('BAUSTEIN.MANUELL')),
    );
}
?>

<div class="sm-step"><b><?= vw_e(vw_t('LOX.S8_TITEL')) ?></b><br>
<?= vw_t('LOX.S8_TEXT') ?>
<table class="sm-tbl">
<tr><th>#</th><th><?= vw_e(vw_t('LOX.T_BAUSTEIN')) ?></th><th><?= vw_e(vw_t('LOX.T_NAMENSVORSCHLAG')) ?></th>
    <th><?= vw_e(vw_t('LOX.T_PARAMETER')) ?></th><th><?= vw_e(vw_t('LOX.T_EINGAENGE')) ?></th></tr>
<?php foreach (vw_bausteine() as $vw_b) { ?>
<tr><td><?= (int) $vw_b[0] ?></td><td><?= vw_t($vw_b[1]) ?></td><td><?= vw_t($vw_b[2]) ?></td>
    <td><?= $vw_b[3] !== '' ? vw_t($vw_b[3]) : '&mdash;' ?></td><td><?= $vw_b[4] ?></td></tr>
<?php } ?>
</table>
<?= vw_t('LOX.S8_ERLAEUTERUNG') ?>
</div>

<div class="sm-step"><b><?= vw_e(vw_t('LOX.S9_TITEL')) ?></b><br>
<?= vw_t('LOX.S9_TEXT') ?>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('LOX.T_PRUEFUNG')) ?></th><th><?= vw_e(vw_t('LOX.T_ERWARTUNG')) ?></th></tr>
<tr><td><span class="sm-mono"><?= vw_e($vw_basis) ?>?token=<?= vw_e($vw_token) ?>&amp;aktion=status</span></td>
    <td><span class="sm-mono">VOLKSWAGEN;OK=1;SOC=...</span></td></tr>
<tr><td><span class="sm-mono"><?= vw_e($vw_basis) ?>?aktion=status</span></td>
    <td><span class="sm-mono">FEHLER;OK=0;GRUND=TOKEN</span> (HTTP 403)</td></tr>
<tr><td><span class="sm-mono"><?= vw_e($vw_basis) ?>?token=<?= vw_e($vw_token) ?>&amp;aktion=quatsch</span></td>
    <td><span class="sm-mono">FEHLER;OK=0;GRUND=UNBEKANNTE_AKTION</span> (HTTP 400)</td></tr>
</table>
</div>
</div>

<!-- ================= Reiter: Test ================= -->
<div class="sm-seite<?= $vw_tab === 'tab-test' ? ' sm-active' : '' ?>" id="tab-test">
<h2><?= vw_e(vw_t('TEST.H_SELBSTPRUEFUNG')) ?></h2>
<p class="sm-hilfe"><?= vw_t('TEST.EINLEITUNG') ?></p>
<table class="sm-tbl">
<tr><th style="width:36px;">&nbsp;</th><th><?= vw_e(vw_t('TEST.T_FRAGE')) ?></th><th><?= vw_e(vw_t('TEST.T_BEFUND')) ?></th></tr>
<?php foreach (vw_pruefungen() as $vw_z) { ?>
<tr><td style="text-align:center;"><?php
    if ($vw_z['stand'] === 1) { echo '<span class="sm-an">&#10004;</span>'; }
    elseif ($vw_z['stand'] === 0) { echo '<span class="sm-aus">&#10008;</span>'; }
    else { echo '<span style="color:#888;">&#9679;</span>'; }
?></td><td><?= $vw_z['frage'] ?></td><td><?= $vw_z['antwort'] ?></td></tr>
<?php } ?>
</table>

<div class="sm-legende">
<span><i class="sm-punkt sm-b-lesen"></i> <?= vw_t('LEGENDE.LESEN') ?></span>
<span><i class="sm-punkt sm-b-technik"></i> <?= vw_t('LEGENDE.TECHNIK') ?></span>
<span><i class="sm-punkt sm-b-aktion"></i> <?= vw_t('LEGENDE.AKTION') ?></span>
</div>

<h3><?= vw_e(vw_t('TEST.H_LESEN')) ?></h3>
<div class="sm-knopfreihe">
  <a class="sm-btn sm-b-lesen" href="<?= vw_e($vw_basis) ?>?token=<?= vw_e($vw_token) ?>&amp;aktion=status&amp;fahrzeug=1" target="_blank"><?= vw_e(vw_t('TEST.K_STATUS')) ?></a>
  <a class="sm-btn sm-b-lesen" href="<?= vw_e($vw_basis) ?>?token=<?= vw_e($vw_token) ?>&amp;aktion=laden&amp;fahrzeug=1" target="_blank"><?= vw_e(vw_t('TEST.K_LADEN')) ?></a>
  <a class="sm-btn sm-b-lesen" href="<?= vw_e($vw_basis) ?>?token=<?= vw_e($vw_token) ?>&amp;aktion=wartung&amp;fahrzeug=1" target="_blank"><?= vw_e(vw_t('TEST.K_WARTUNG')) ?></a>
  <a class="sm-btn sm-b-lesen" href="<?= vw_e($vw_basis) ?>?token=<?= vw_e($vw_token) ?>&amp;aktion=fahrzeuge" target="_blank"><?= vw_e(vw_t('TEST.K_FAHRZEUGE')) ?></a>
</div>

<h3><?= vw_e(vw_t('TEST.H_TECHNIK')) ?></h3>
<div class="sm-knopfreihe">
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-test">
    <button data-role="none" class="sm-btn sm-b-technik" type="submit" name="selbsttest" value="1"><?= vw_e(vw_t('TEST.K_SELBSTTEST')) ?></button>
  </form>
  <a class="sm-btn sm-b-technik" href="<?= vw_e($vw_basis) ?>?token=<?= vw_e($vw_token) ?>&amp;aktion=roh" target="_blank"><?= vw_e(vw_t('TEST.K_ROH')) ?></a>
</div>
<?php if ($vw_testausgabe !== '') { ?>
<div class="sm-pre"><?= vw_e($vw_testausgabe) ?></div>
<?php } ?>

<h3><?= vw_e(vw_t('TEST.H_SCHALTEN')) ?></h3>
<div class="sm-warnung"><?= vw_t('TEST.SCHALTEN_WARNUNG') ?></div>
<?php if (empty($vw_cfg['steuerung_ein'])) { ?>
<div class="sm-hinweis"><?= vw_t('TEST.SCHALTEN_GESPERRT') ?></div>
<?php } ?>
<form action="index.php" method="post">
<input data-role="none" type="hidden" name="activetab" value="tab-test">
<div class="sm-feld">
  <label for="test_fahrzeug"><?= vw_e(vw_t('TEST.L_FAHRZEUG')) ?></label>
  <input data-role="none" type="number" id="test_fahrzeug" name="test_fahrzeug" value="1" min="1" max="99">
</div>
<div class="sm-feld">
  <label for="test_temp"><?= vw_e(vw_t('TEST.L_TEMP')) ?></label>
  <input data-role="none" type="text" id="test_temp" name="test_temp" value="<?= (int) $vw_cfg['temp_min'] ?>">
  <div class="sm-hilfe"><?= vw_t('TEST.H_TEMP') ?></div>
</div>
<div class="sm-feld">
  <label for="test_prozent"><?= vw_e(vw_t('TEST.L_PROZENT')) ?></label>
  <input data-role="none" type="number" id="test_prozent" name="test_prozent" value="80" min="10" max="100">
  <div class="sm-hilfe"><?= vw_t('TEST.H_PROZENT') ?></div>
</div>
<div class="sm-feld">
  <label for="test_ampere"><?= vw_e(vw_t('TEST.L_AMPERE')) ?></label>
  <select data-role="none" id="test_ampere" name="test_ampere">
    <option value="5">5 A</option>
    <option value="6">6 A</option>
    <option value="10">10 A</option>
    <option value="13">13 A</option>
    <option value="16" selected>16 A</option>
    <option value="32">32 A</option>
  </select>
  <div class="sm-hilfe"><?= vw_t('TEST.H_AMPERE') ?></div>
</div>
<div class="sm-knopfreihe">
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="abruf"><?= vw_e(vw_t('TEST.K_ABRUF')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="klima_start"><?= vw_e(vw_t('TEST.K_KLIMA_EIN')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="klima_stop"><?= vw_e(vw_t('TEST.K_KLIMA_AUS')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="laden_start"><?= vw_e(vw_t('TEST.K_LADEN_EIN')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="laden_stop"><?= vw_e(vw_t('TEST.K_LADEN_AUS')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="ladegrenze"><?= vw_e(vw_t('TEST.K_LADEGRENZE')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="ladestrom"><?= vw_e(vw_t('TEST.K_LADESTROM')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="scheibe_ein"><?= vw_e(vw_t('TEST.K_SCHEIBE_EIN')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="scheibe_aus"><?= vw_e(vw_t('TEST.K_SCHEIBE_AUS')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="wecken"><?= vw_e(vw_t('TEST.K_WECKEN')) ?></button>
</div>
</form>

<div class="sm-warnung"><b><?= vw_e(vw_t('TEST.H_UNGEPRUEFT')) ?></b><br><?= vw_t('TEST.UNGEPRUEFT') ?></div>
</div>

<!-- ================= Reiter: Logdateien ================= -->
<div class="sm-seite<?= $vw_tab === 'tab-log' ? ' sm-active' : '' ?>" id="tab-log">
<h2><?= vw_e(vw_t('LOG.H_TITEL')) ?></h2>
<?php
if (class_exists('LBWeb', false) && method_exists('LBWeb', 'loglist_html')) {
    echo LBWeb::loglist_html();
}
?>
<p class="sm-hilfe"><?= vw_t('LOG.ERKLAERUNG') ?><br>
<span class="sm-mono"><?= vw_e($vw_p['log']) ?></span></p>
<?php if ($vw_logzeilen) { ?>
<div class="sm-log"><?= vw_e(implode("\n", $vw_logzeilen)) ?></div>
<?php } else { ?>
<div class="sm-hinweis"><?= vw_t('LOG.LEER') ?></div>
<?php } ?>
<div class="sm-legende">
<span><i class="sm-punkt sm-b-aktion"></i> <?= vw_t('LEGENDE.AKTION_LOG') ?></span>
</div>
<div class="sm-knopfreihe">
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-log">
    <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="log_leeren" value="1"><?= vw_e(vw_t('LOG.K_LEEREN')) ?></button>
  </form>
</div>
</div>

</div><!-- /sm-wrap -->

<script>
(function () {
	var reiter = document.querySelectorAll('.sm-tab');
	function zeige(id) {
		reiter.forEach(function (r) { r.classList.toggle('sm-active', r.dataset.ziel === id); });
		document.querySelectorAll('.sm-seite').forEach(function (s) { s.classList.toggle('sm-active', s.id === id); });
		document.querySelectorAll('input[name="activetab"]').forEach(function (f) { f.value = id; });
		if (history.replaceState) { history.replaceState(null, '', 'index.php?form=' + id.replace('tab-', '')); }
	}
	reiter.forEach(function (r) {
		r.addEventListener('click', function (e) { e.preventDefault(); zeige(r.dataset.ziel); });
	});
	zeige(<?= json_encode($vw_tab) ?>);
})();
</script>
<?php
if ($vw_rahmen) {
    LBWeb::lbfooter();
}
