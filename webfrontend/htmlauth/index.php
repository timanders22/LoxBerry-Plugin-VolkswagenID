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

/* ==================================================================
 * DIE REIHENFOLGE IST BAUVORSCHRIFT, NICHT GESCHMACKSSACHE
 * ==================================================================
 *
 *   1. Bibliothek laden                     (steht oben)
 *   2. Konfiguration lesen, Merkwort erzeugen
 *   3. WACHPOSTEN
 *   4. Reiterwahl
 *   5. ALLE Handler - darunter jeder Download, der mit exit endet
 *   6. ERST JETZT LBWeb::lbheader()
 *   7. HTML
 *
 * Zu 5 und 6: stand der Kopf davor, war er beim Aufruf von header() schon
 * geschrieben - "Cannot modify header information", und der Knopf
 * "Einstellungen sichern" lieferte eine Seite mit angehaengtem JSON statt
 * einer Datei. Am PHP-CLI ist das unsichtbar: header() ist dort wirkungslos
 * und headers_sent() immer falsch.
 *
 * Zu 3 und 4: der Wachposten steht VOR der Reiterwahl. Sonst uebernimmt sie
 * das activetab eines abgewiesenen POST, und ein fremdes Formular kann
 * wenigstens noch den Reiter umschalten.
 * ================================================================== */

$vw_meldungen = array();   // Erfolgsmeldungen
$vw_fehler = array();      // Beanstandungen - gesammelt, nicht ueberschrieben
$vw_testausgabe = '';
$vw_post = (isset($_SERVER['REQUEST_METHOD']) ? $_SERVER['REQUEST_METHOD'] : '') === 'POST';

/* ---------------- 2. Konfiguration und Merkwort ---------------- */
$vw_cfg = vw_config();
$vw_token = vw_token();
$vw_lage = vw_config_lesen(true);

/* ==================================================================
 * 3. WACHPOSTEN
 * ==================================================================
 *
 * htmlauth schuetzt gegen den unangemeldeten Aufruf - nicht dagegen, dass der
 * Browser eines angemeldeten Bedieners ein Formular abschickt, das auf einer
 * fremden Seite steht. Bis 0.9.9 gab es das Merkmal nicht, obwohl der
 * Kommentar an dieser Stelle es beschrieb. Am Pruefstand gemessen
 * (27.08.2026): ein einziger fremder POST erzeugte ein neues Aktionstoken
 * (danach beantwortet der Endpunkt jeden Virtuellen Ausgang mit 403, und ein
 * Virtueller Ausgang wertet die Antwort nicht aus - der Ausfall bleibt still),
 * ein zweiter legte einen Klimabefehl in die Warteschlange, ein dritter
 * loeschte die Volkswagen-Zugangsdaten samt Zweitschrift.
 *
 * Geprueft wird an EINER Stelle vor allen Handlern, und faellt die Pruefung
 * durch, wird $_POST bis auf den aktiven Reiter GELEERT. Das ist mit Absicht
 * gruendlicher als eine Abfrage vor jedem Handler: der naechste Handler, den
 * jemand ergaenzt, ist damit von selbst mitgeschuetzt. Ein Schutz, den man
 * beim Erweitern vergessen kann, ist keiner.
 *
 * Und es wird GEMELDET. Ein Formular, das wortlos nichts tut, schickt den
 * Anwender auf die Suche nach einem Fehler, den es nicht gibt.
 * ================================================================== */
if ($vw_post && !vw_formtoken_ok($vw_cfg)) {
    $vw_behalten = isset($_POST['activetab']) && is_string($_POST['activetab'])
                 ? $_POST['activetab'] : null;
    $_POST = array();
    if ($vw_behalten !== null) {
        $_POST['activetab'] = $vw_behalten;
    }
    $vw_post = false;
    $vw_fehler[] = vw_t('ALLG.WACHPOSTEN');
}

/* ---------------- 4. Reiterwahl ----------------
 * EINE Quelle fuer Reihenfolge, Positivliste und Beschriftung.
 *
 * Bis 0.9.0 standen die Reiternamen an drei Stellen: in diesem Muster, in
 * der Reiterleiste und in den Flaechen-ids. Wer einen Reiter ergaenzt
 * und eine davon vergisst, bekommt keinen Fehler, sondern eine Seite, die
 * nach jedem Absenden auf Einstellungen zurueckspringt. */
$vw_reiter_ids = array('settings', 'mqtt', 'loxone', 'verlauf', 'test', 'log');
$vw_muster = '/^tab-(' . implode('|', $vw_reiter_ids) . ')$/';
$vw_tab = 'tab-settings';
if (isset($_POST['activetab']) && is_string($_POST['activetab'])
    && preg_match($vw_muster, (string) $_POST['activetab'])) {
    $vw_tab = (string) $_POST['activetab'];
} elseif (isset($_GET['form']) && is_string($_GET['form'])
          && preg_match($vw_muster, 'tab-' . (string) $_GET['form'])) {
    $vw_tab = 'tab-' . (string) $_GET['form'];
}

/* ==================================================================
 * 5. HANDLER - alle vor lbheader()
 * ================================================================== */

/* ---------------- Vorlagen herunterladen ----------------
 * Fuenf Eingangsarten, dazu die Ausgangsvorlage und die MQTT-Vorlage. Bis
 * 0.9.9 gab es nur den Status-Eingang fuer Fahrzeug 1. */
if ($vw_post && isset($_POST['vorlage'])) {
    $vw_nr = isset($_POST['vorlage_nr']) && preg_match('/^[0-9]{1,2}$/', (string) $_POST['vorlage_nr'])
           ? (int) $_POST['vorlage_nr'] : 1;
    $vw_art = (string) $_POST['vorlage'];
    if ($vw_art === 'befehle') {
        list($vw_name, $vw_inhalt) = vw_vorlage_vo($vw_nr);
    } elseif ($vw_art === 'mqtt') {
        list($vw_name, $vw_inhalt) = vw_vorlage_mqtt($vw_nr);
    } elseif (in_array($vw_art, vw_vorlagenarten(), true)) {
        list($vw_name, $vw_inhalt) = vw_vorlage($vw_nr, $vw_art);
    } else {
        $vw_name = '';
        $vw_inhalt = '';
        $vw_fehler[] = vw_t('LOX.VORLAGE_UNBEKANNT');
    }
    if ($vw_name !== '') {
        header('Content-Type: application/xml; charset=utf-8');
        header('Content-Disposition: attachment; filename="' . $vw_name . '"');
        echo $vw_inhalt;
        exit;
    }
}

/* ---------------- Einstellungen sichern ----------------
 *
 * Ausgegeben wird die VOLLE Konfiguration - samt Aktionstoken. Ohne ihn
 * stuenden nach dem Zurueckspielen alle Felder richtig, und das Plugin
 * kaeme trotzdem nicht an die Anlage; die Datei waere wertlos. Damit
 * traegt sie ein Geheimnis, und der Hinweis am Knopf sagt das.
 *
 * Der Handler steht hier oben, nicht unten: bis 0.9.9 stand er NACH dem
 * Laden der Anzeigewerte, und das Zurueckspielen zeigte danach jeden Wert
 * auf altem Stand. */
if ($vw_post && isset($_POST['vw_sichern'])) {
    $vw_js = vw_sicherung_schreiben();
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
 * Plugins ist wenige Kilobyte gross; alles darueber wird gar nicht gelesen.
 *
 * Nach dem Uebernehmen wird der Zwischenspeicher der Konfiguration geleert
 * und alles neu gelesen. Bis 0.9.9 fehlte das: die Datei trug danach die
 * neuen Werte, die Seite zeigte neunzehnmal das alte Aktionstoken und jedes
 * Feld auf altem Stand. Wer daraufhin auf Speichern drueckte - naheliegend,
 * weil es aussah, als sei nichts angekommen -, schrieb den alten Stand
 * zurueck. Gemessen am 27.08.2026. */
if ($vw_post && isset($_POST['vw_zurueck'])) {
    if (!isset($_FILES['vw_sicherung']) || !is_array($_FILES['vw_sicherung'])
        || !isset($_FILES['vw_sicherung']['tmp_name'])
        || !@is_uploaded_file($_FILES['vw_sicherung']['tmp_name'])) {
        $vw_fehler[] = vw_t('EINST.SICH_KEINE_DATEI');
    } elseif ((int) $_FILES['vw_sicherung']['size'] > 65536) {
        $vw_fehler[] = vw_t('EINST.SICH_ZU_GROSS');
    } else {
        list($vw_neu, $vw_mangel, $vw_n) = vw_sicherung_lesen(
            (string) @file_get_contents($_FILES['vw_sicherung']['tmp_name']));
        if ($vw_neu === null) {
            /* ALLE Beanstandungen, nicht nur die erste - und geaendert wird
             * nichts. Eine zur Haelfte uebernommene Konfiguration ist
             * schlimmer als die alte, und man sieht es ihr nicht an. */
            $vw_fehler[] = vw_t('EINST.SICH_ABGELEHNT') . ' ' . implode(' ', $vw_mangel);
        } elseif (vw_config_speichern($vw_neu)) {
            $vw_meldungen[] = sprintf(vw_t('EINST.SICH_UEBERNOMMEN'), $vw_n);
            /* Den Dienst nachziehen und SAGEN, was mit ihm geschehen ist.
             * Der Dienst liest die Konfiguration bei jedem Takt neu; ein
             * Neustart ist deshalb nur noetig, wenn er gerade laeuft und der
             * Takt lang ist. Gesagt wird es in jedem Fall. */
            if (vw_dienst_pid() > 0) {
                list($vw_dok, $vw_daus) = vw_dienst('restart');
                $vw_meldungen[] = $vw_dok ? vw_t('EINST.SICH_DIENST_NEU')
                                          : vw_t('EINST.SICH_DIENST_FEHL') . ' ' . vw_e($vw_daus);
            } else {
                $vw_meldungen[] = vw_t('EINST.SICH_DIENST_AUS');
            }
        } else {
            $vw_fehler[] = vw_t('EINST.SICH_SCHREIBFEHLER');
        }
    }
    $vw_tab = 'tab-settings';
}

/* ---------------- Einstellungen speichern ---------------- */
if ($vw_post && isset($_POST['speichern'])) {
    $vw_cfg = vw_config();

    /* Die Grenzen kommen aus vw_regeln() - derselben Quelle, gegen die auch
     * die Sicherungsdatei und die Konfiguration beim Lesen geprueft werden.
     * Eine zweite Wahrheit ueber zulaessige Werte gibt es nicht. */
    $vw_regeln = vw_regeln();
    foreach (array('intervall', 'takt_wartung', 'temp_min', 'temp_max', 'verlauf_tage',
                   'wartezeit', 'wartezeit_endpunkt', 'heim_radius', 'abstand_abruf',
                   'befehle_stunde', 'entprellung', 'abfahrt_vorlauf', 'abfahrt_temp')
             as $vw_feld) {
        $vw_wert = isset($_POST[$vw_feld]) && is_string($_POST[$vw_feld])
                 ? trim((string) $_POST[$vw_feld]) : '';
        if (!preg_match('/^[0-9]+$/', $vw_wert)) {
            $vw_fehler[] = sprintf(vw_t('EINST.FEHLER_ZAHL'), vw_t('EINST.L_' . strtoupper($vw_feld)));
            continue;
        }
        list($vw_ok2, $vw_rein) = vw_wert_pruefen($vw_feld, $vw_wert);
        if (!$vw_ok2) {
            $vw_g = $vw_regeln[$vw_feld];
            $vw_fehler[] = sprintf(vw_t('EINST.FEHLER_BEREICH'),
                vw_t('EINST.L_' . strtoupper($vw_feld)), $vw_g[1], $vw_g[2]);
            continue;
        }
        $vw_cfg[$vw_feld] = $vw_rein;
    }
    if (isset($vw_cfg['temp_min'], $vw_cfg['temp_max'])
        && $vw_cfg['temp_min'] > $vw_cfg['temp_max']) {
        $vw_fehler[] = vw_t('EINST.FEHLER_TEMP_TAUSCH');
    }

    $vw_cfg['steuerung_ein'] = isset($_POST['steuerung_ein']) ? 1 : 0;
    $vw_cfg['zugriff_erzwingen'] = isset($_POST['zugriff_erzwingen']) ? 1 : 0;
    $vw_cfg['eingreifend_ein'] = isset($_POST['eingreifend_ein']) ? 1 : 0;
    $vw_cfg['abfahrt_ein'] = isset($_POST['abfahrt_ein']) ? 1 : 0;
    $vw_cfg['empf_kleiner'] = isset($_POST['empf_kleiner']) ? 1 : 0;

    /* Heimatort und Ladeempfehlung: Kommazahlen und Themen. Ein LEERES Feld
     * ist hier zulaessig und heisst "nicht eingerichtet" - es wird nicht zu 0
     * gemacht. Eine 0/0 waere ein Punkt im Atlantik, und jede Entfernung
     * daraus waere eine Zahl, die richtig aussieht. */
    foreach (array('heim_breite', 'heim_laenge', 'empf_grenze') as $vw_feld) {
        $vw_wert = isset($_POST[$vw_feld]) && is_string($_POST[$vw_feld])
                 ? trim((string) $_POST[$vw_feld]) : '';
        if ($vw_wert === '') {
            $vw_cfg[$vw_feld] = '';
            continue;
        }
        list($vw_ok2, $vw_rein) = vw_wert_pruefen($vw_feld, $vw_wert);
        if (!$vw_ok2) {
            $vw_g = $vw_regeln[$vw_feld];
            $vw_fehler[] = sprintf(vw_t('EINST.FEHLER_BEREICH'),
                vw_t('EINST.L_' . strtoupper($vw_feld)), $vw_g[1], $vw_g[2]);
            continue;
        }
        $vw_cfg[$vw_feld] = $vw_rein;
    }
    /* Ein Heimatort ist ein PAAR. Nur eine Haelfte ergibt keine Entfernung,
     * und ein Feld, das nichts bewirkt, ist schlimmer als ein fehlendes. */
    if (($vw_cfg['heim_breite'] === '') !== ($vw_cfg['heim_laenge'] === '')) {
        $vw_fehler[] = vw_t('EINST.FEHLER_HEIM_PAAR');
    }
    foreach (array('empf_thema', 'abfahrt_thema') as $vw_feld) {
        $vw_wert = isset($_POST[$vw_feld]) && is_string($_POST[$vw_feld])
                 ? trim((string) $_POST[$vw_feld]) : '';
        list($vw_ok2, $vw_rein) = vw_wert_pruefen($vw_feld, $vw_wert);
        if (!$vw_ok2) {
            $vw_fehler[] = sprintf(vw_t('EINST.FEHLER_THEMA'),
                vw_t('EINST.L_' . strtoupper($vw_feld)));
            continue;
        }
        $vw_cfg[$vw_feld] = $vw_rein;
    }
    /* Eine Grenze ohne Thema wirkt nicht, ein Thema ohne Grenze auch nicht. */
    if (($vw_cfg['empf_thema'] === '') !== ($vw_cfg['empf_grenze'] === '')) {
        $vw_fehler[] = vw_t('EINST.FEHLER_EMPF_PAAR');
    }
    if ($vw_cfg['abfahrt_ein'] && $vw_cfg['abfahrt_thema'] === '') {
        $vw_fehler[] = vw_t('EINST.FEHLER_ABFAHRT_THEMA');
    }


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
    $vw_mcfg['mqtt_retain'] = isset($_POST['mqtt_retain']) ? 1 : 0;
    $vw_mtopic = trim(preg_replace('/[\x00-\x1F\x7F"\']/', '',
        (string) (isset($_POST['mqtt_topic']) && is_string($_POST['mqtt_topic'])
                  ? $_POST['mqtt_topic'] : '')));
    $vw_mtopic = trim($vw_mtopic, '/');
    list($vw_mok, $vw_mrein) = vw_wert_pruefen('mqtt_topic', $vw_mtopic);
    if (!$vw_mok) {
        $vw_fehler[] = vw_t('EINST.FEHLER_TOPIC');
    } else {
        $vw_mcfg['mqtt_topic'] = $vw_mrein;
    }
    if (!$vw_fehler) {
        if (vw_config_speichern($vw_mcfg)) {
            $vw_meldungen[] = vw_t('EINST.GESPEICHERT');
        } else {
            $vw_fehler[] = sprintf(vw_t('EINST.FEHLER_SPEICHERN'), $vw_p['config']);
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

/* ==================================================================
 * Laden - NACH allen Handlern, damit die Anzeige den neuen Stand zeigt
 * ================================================================== */
$vw_cfg = vw_config();
$vw_token = vw_token();
$vw_lage = vw_config_lesen(true);
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
    <span class="sm-hilfe"><?= vw_e(vw_t('ALLG.GATEWAY')) ?><?= (int) $vw_mqtt['fassung'] > 0 ? ' V' . (int) $vw_mqtt['fassung'] : '' ?></span>
  </div>
  <?php
  /* Das Lebenszeichen. Es haengt NICHT am Abbild: ein Abruf kann
   * fehlschlagen, waehrend der Dienst tadellos arbeitet. -1 heisst
   * "noch nie gelaufen"; 0 waere ein gueltiger Stand. */
  $vw_zaehler = isset($vw_zustand['zaehler']) && is_numeric($vw_zustand['zaehler'])
              ? (int) $vw_zustand['zaehler'] : -1;
  ?>
  <div class="sm-kachel"><?= vw_e(vw_t('ALLG.LEBENSZEICHEN')) ?>
    <b class="<?= $vw_zaehler >= 0 ? 'sm-an' : 'sm-aus' ?>"><?= $vw_zaehler < 0 ? '&ndash;' : (int) $vw_zaehler ?></b>
    <span class="sm-hilfe"><?= vw_e(vw_t('ALLG.LEBENSZEICHEN_H')) ?></span>
  </div>
</div>

<?php if (!empty($vw_zustand['fehler'])) { ?>
<div class="sm-warnung"><b><?= vw_e(vw_t('ALLG.LETZTE_STOERUNG')) ?></b>
<?= vw_e($vw_zustand['fehler']) ?>
<?php if (!empty($vw_zustand['grund'])) { ?><span class="sm-mono"><?= vw_e($vw_zustand['grund']) ?></span><?php } ?>
</div>
<?php } ?>

<?php
/* Die Lage der Konfiguration - jeder Zustand, den der Code erzeugen kann,
 * braucht seinen Satz. Ein stiller Rueckgriff auf die Zweitschrift ist eine
 * Auskunft, die der Anwender bekommen muss: sie heisst, dass die lebende
 * Datei fehlte oder unbrauchbar war. */
if ($vw_lage['lage'] !== 'ok') { ?>
<div class="sm-warnung"><b><?= vw_e(vw_t('ALLG.KONFIGLAGE')) ?></b>
<?= vw_t('ALLG.KONFIG_' . strtoupper($vw_lage['lage'])) ?></div>
<?php }
if ($vw_lage['abgewiesen']) { ?>
<div class="sm-warnung"><b><?= vw_e(vw_t('ALLG.KONFIG_ABGEWIESEN')) ?></b>
<?php foreach ($vw_lage['abgewiesen'] as $vw_k => $vw_v) { ?>
<br><span class="sm-mono"><?= vw_e($vw_k) ?></span> = <span class="sm-mono"><?= vw_e(substr((string) $vw_v, 0, 40)) ?></span>
<?php } ?>
</div>
<?php }
if ($vw_lage['fremd']) { ?>
<div class="sm-warnung"><b><?= vw_e(vw_t('ALLG.KONFIG_FREMD')) ?></b>
<span class="sm-mono"><?= vw_e(implode(', ', $vw_lage['fremd'])) ?></span></div>
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
    'settings' => 'REITER.EINSTELLUNGEN', 'mqtt'    => '', 'loxone' => 'REITER.LOXONE',
    'verlauf'  => 'REITER.VERLAUF',       'test'    => 'REITER.TEST',
    'log'      => 'REITER.LOG',
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
    <?= vw_formfeld($vw_cfg) ?>
    <button data-role="none" class="sm-btn sm-b-lesen" type="submit" name="dienst" value="start"><?= vw_e(vw_t('EINST.K_START')) ?></button>
  </form>
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-settings">
    <?= vw_formfeld($vw_cfg) ?>
    <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="dienst" value="restart"><?= vw_e(vw_t('EINST.K_NEUSTART')) ?></button>
  </form>
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-settings">
    <?= vw_formfeld($vw_cfg) ?>
    <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="dienst" value="stop"><?= vw_e(vw_t('EINST.K_STOPP')) ?></button>
  </form>
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-settings">
    <?= vw_formfeld($vw_cfg) ?>
    <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="sitzung_verwerfen" value="1"><?= vw_e(vw_t('EINST.K_SITZUNG')) ?></button>
  </form>
</div>

<form action="index.php" method="post" autocomplete="off">
<input data-role="none" type="hidden" name="speichern" value="1">
<input data-role="none" type="hidden" name="activetab" value="tab-settings">
<?= vw_formfeld($vw_cfg) ?>

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
<div class="sm-feld">
  <label for="wartezeit_endpunkt"><?= vw_e(vw_t('EINST.L_WARTEZEIT_ENDPUNKT')) ?></label>
  <input data-role="none" type="number" id="wartezeit_endpunkt" name="wartezeit_endpunkt" value="<?= (int) $vw_cfg['wartezeit_endpunkt'] ?>" min="0" max="15">
  <div class="sm-hilfe"><?= vw_t('EINST.H_WARTEZEIT_ENDPUNKT') ?></div>
</div>

<h2><?= vw_e(vw_t('EINST.H_EINGREIFEND')) ?></h2>
<div class="sm-warnung"><?= vw_t('EINST.EINGREIFEND_ERKLAERUNG') ?></div>
<div class="sm-feld">
  <label style="display:inline-flex;align-items:center;gap:8px;">
    <input data-role="none" type="checkbox" name="eingreifend_ein" value="1" <?= !empty($vw_cfg['eingreifend_ein']) ? 'checked' : '' ?>>
    <?= vw_e(vw_t('EINST.L_EINGREIFEND_EIN')) ?>
  </label>
  <div class="sm-hilfe"><?= vw_t('EINST.H_EINGREIFEND_EIN') ?></div>
</div>
<?php if (!empty($vw_cfg['eingreifend_ein']) && $vw_zg['spin_laenge'] !== 4) { ?>
<div class="sm-warnung"><?= vw_t('EINST.EINGREIFEND_OHNE_SPIN') ?></div>
<?php } ?>

<h2><?= vw_e(vw_t('EINST.H_BREMSE')) ?></h2>
<div class="sm-warnung"><?= vw_t('EINST.BREMSE_ERKLAERUNG') ?></div>
<div class="sm-feld">
  <label for="abstand_abruf"><?= vw_e(vw_t('EINST.L_ABSTAND_ABRUF')) ?></label>
  <input data-role="none" type="number" id="abstand_abruf" name="abstand_abruf" value="<?= (int) $vw_cfg['abstand_abruf'] ?>" min="0" max="3600">
  <div class="sm-hilfe"><?= vw_t('EINST.H_ABSTAND_ABRUF') ?></div>
</div>
<div class="sm-feld">
  <label for="befehle_stunde"><?= vw_e(vw_t('EINST.L_BEFEHLE_STUNDE')) ?></label>
  <input data-role="none" type="number" id="befehle_stunde" name="befehle_stunde" value="<?= (int) $vw_cfg['befehle_stunde'] ?>" min="1" max="240">
  <div class="sm-hilfe"><?= vw_t('EINST.H_BEFEHLE_STUNDE') ?></div>
</div>
<div class="sm-feld">
  <label for="entprellung"><?= vw_e(vw_t('EINST.L_ENTPRELLUNG')) ?></label>
  <input data-role="none" type="number" id="entprellung" name="entprellung" value="<?= (int) $vw_cfg['entprellung'] ?>" min="0" max="600">
  <div class="sm-hilfe"><?= vw_t('EINST.H_ENTPRELLUNG') ?></div>
</div>

<h2><?= vw_e(vw_t('EINST.H_HEIMAT')) ?></h2>
<div class="sm-hinweis"><?= vw_t('EINST.HEIMAT_ERKLAERUNG') ?></div>
<div class="sm-feld">
  <label for="heim_breite"><?= vw_e(vw_t('EINST.L_HEIM_BREITE')) ?></label>
  <input data-role="none" type="text" id="heim_breite" name="heim_breite" value="<?= vw_e($vw_cfg['heim_breite']) ?>" placeholder="48.137200">
</div>
<div class="sm-feld">
  <label for="heim_laenge"><?= vw_e(vw_t('EINST.L_HEIM_LAENGE')) ?></label>
  <input data-role="none" type="text" id="heim_laenge" name="heim_laenge" value="<?= vw_e($vw_cfg['heim_laenge']) ?>" placeholder="11.575600">
  <div class="sm-hilfe"><?= vw_t('EINST.H_HEIM_KOORDINATEN') ?></div>
</div>
<div class="sm-feld">
  <label for="heim_radius"><?= vw_e(vw_t('EINST.L_HEIM_RADIUS')) ?></label>
  <input data-role="none" type="number" id="heim_radius" name="heim_radius" value="<?= (int) $vw_cfg['heim_radius'] ?>" min="10" max="5000">
  <div class="sm-hilfe"><?= vw_t('EINST.H_HEIM_RADIUS') ?></div>
</div>

<h2><?= vw_e(vw_t('EINST.H_EMPFEHLUNG')) ?></h2>
<div class="sm-hinweis"><?= vw_t('EINST.EMPFEHLUNG_ERKLAERUNG') ?></div>
<?php if (!empty($vw_zustand['horcher_grund'])) { ?>
<div class="sm-warnung"><?= vw_e($vw_zustand['horcher_grund']) ?></div>
<?php } ?>
<div class="sm-feld">
  <label for="empf_thema"><?= vw_e(vw_t('EINST.L_EMPF_THEMA')) ?></label>
  <input data-role="none" type="text" id="empf_thema" name="empf_thema" value="<?= vw_e($vw_cfg['empf_thema']) ?>" placeholder="awattar/jetzt/preis">
  <div class="sm-hilfe"><?= vw_t('EINST.H_EMPF_THEMA') ?></div>
</div>
<div class="sm-feld">
  <label for="empf_grenze"><?= vw_e(vw_t('EINST.L_EMPF_GRENZE')) ?></label>
  <input data-role="none" type="text" id="empf_grenze" name="empf_grenze" value="<?= vw_e($vw_cfg['empf_grenze']) ?>" placeholder="12.5">
</div>
<div class="sm-feld">
  <label style="display:inline-flex;align-items:center;gap:8px;">
    <input data-role="none" type="checkbox" name="empf_kleiner" value="1" <?= !empty($vw_cfg['empf_kleiner']) ? 'checked' : '' ?>>
    <?= vw_e(vw_t('EINST.L_EMPF_KLEINER')) ?>
  </label>
  <div class="sm-hilfe"><?= vw_t('EINST.H_EMPF_KLEINER') ?></div>
</div>

<h2><?= vw_e(vw_t('EINST.H_ABFAHRT')) ?></h2>
<div class="sm-hinweis"><?= vw_t('EINST.ABFAHRT_ERKLAERUNG') ?></div>
<div class="sm-feld">
  <label style="display:inline-flex;align-items:center;gap:8px;">
    <input data-role="none" type="checkbox" name="abfahrt_ein" value="1" <?= !empty($vw_cfg['abfahrt_ein']) ? 'checked' : '' ?>>
    <?= vw_e(vw_t('EINST.L_ABFAHRT_EIN')) ?>
  </label>
</div>
<div class="sm-feld">
  <label for="abfahrt_thema"><?= vw_e(vw_t('EINST.L_ABFAHRT_THEMA')) ?></label>
  <input data-role="none" type="text" id="abfahrt_thema" name="abfahrt_thema" value="<?= vw_e($vw_cfg['abfahrt_thema']) ?>" placeholder="abfahrt/ABFAHRT_IN">
  <div class="sm-hilfe"><?= vw_t('EINST.H_ABFAHRT_THEMA') ?></div>
</div>
<div class="sm-feld">
  <label for="abfahrt_vorlauf"><?= vw_e(vw_t('EINST.L_ABFAHRT_VORLAUF')) ?></label>
  <input data-role="none" type="number" id="abfahrt_vorlauf" name="abfahrt_vorlauf" value="<?= (int) $vw_cfg['abfahrt_vorlauf'] ?>" min="5" max="180">
</div>
<div class="sm-feld">
  <label for="abfahrt_temp"><?= vw_e(vw_t('EINST.L_ABFAHRT_TEMP')) ?></label>
  <input data-role="none" type="number" id="abfahrt_temp" name="abfahrt_temp" value="<?= (int) $vw_cfg['abfahrt_temp'] ?>" min="10" max="30">
  <div class="sm-hilfe"><?= vw_t('EINST.H_ABFAHRT_TEMP') ?></div>
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
    <?= vw_formfeld($vw_cfg) ?>
    <button data-role="none" class="sm-btn sm-b-lesen" type="submit" name="vw_sichern" value="1"><?= vw_t('EINST.K_SICHERN') ?></button>
  </form>
  <form action="index.php" method="post" enctype="multipart/form-data">
    <input data-role="none" type="hidden" name="activetab" value="tab-settings">
    <?= vw_formfeld($vw_cfg) ?>
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
<?= vw_formfeld($vw_cfg) ?>
<div class="sm-feld">
  <label style="display:inline-flex;align-items:center;gap:8px;">
    <input data-role="none" type="checkbox" name="mqtt_ein" value="1" <?= !empty($vw_cfg['mqtt_ein']) ? 'checked' : '' ?>>
    <?= vw_e(vw_t('EINST.L_MQTT_EIN')) ?>
  </label>
</div>
<div class="sm-feld">
  <label for="mqtt_topic"><?= vw_e(vw_t('EINST.L_MQTT_TOPIC')) ?></label>
  <input data-role="none" type="text" id="mqtt_topic" name="mqtt_topic" value="<?= vw_e($vw_cfg['mqtt_topic']) ?>" placeholder="volkswagen">
  <div class="sm-hilfe"><?= vw_t('EINST.H_MQTT_TOPIC') ?></div>
</div>
<div class="sm-feld">
  <label style="display:inline-flex;align-items:center;gap:8px;">
    <input data-role="none" type="checkbox" name="mqtt_retain" value="1" <?= !empty($vw_cfg['mqtt_retain']) ? 'checked' : '' ?>>
    <?= vw_e(vw_t('EINST.L_MQTT_RETAIN')) ?>
  </label>
  <div class="sm-hilfe"><?= vw_t('EINST.H_MQTT_RETAIN') ?></div>
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
<?php
/* Der Hinweis UND seine Warnstufe UND die Anleitung daneben verzweigen
 * gemeinsam. Bis 0.9.9 verzweigte nur der Text: vw_abo_text() lieferte unter
 * Gateway V2 richtig "einzutragen ist hier nichts" - und stand in einem roten
 * Warnkasten, gefolgt von der unbedingten Anweisung "folgende Zeile eintragen
 * und speichern". Der V2-Anwender wurde damit zu einem Eingabeplatz
 * geschickt, den es nicht gibt.
 *
 * Drei Ausgaenge, nicht zwei: ist die Fassung nicht feststellbar, werden
 * BEIDE Faelle genannt statt einer behauptet. */
$vw_gw = (int) $vw_mqtt['fassung'];
?>
<div class="<?= $vw_gw >= 2 ? 'sm-hinweis' : 'sm-warnung' ?>"><?= vw_abo_text() ?></div>
<?php if ($vw_gw !== 2) { /* V1 oder unbekannt: die Anleitung wird gebraucht */ ?>
<div class="sm-step">
<?= vw_t('MQTT.ABO_SCHRITTE') ?>
<p><span class="sm-mono"><?= vw_e($vw_cfg['mqtt_topic']) ?>/#</span></p>
</div>
<?php } else { ?>
<div class="sm-step"><?= vw_t('MQTT.ABO_V2_SCHRITTE') ?>
<p><span class="sm-mono"><?= vw_e($vw_cfg['mqtt_topic']) ?>/#</span></p>
</div>
<?php } ?>

<h2><?= vw_e(vw_t('MQTT.H_THEMEN')) ?></h2>
<p class="sm-hilfe"><?= vw_t('MQTT.THEMEN_ERKLAERUNG') ?></p>
<?php
$vw_themen = vw_mqtt_themen();
$vw_zahl_t = count(array_filter($vw_themen, function ($i) { return empty($i['text']); }));
$vw_text_t = count($vw_themen) - $vw_zahl_t;
?>
<p class="sm-hilfe"><?= sprintf(vw_t('MQTT.THEMEN_ZAHL'), count($vw_themen), $vw_zahl_t, $vw_text_t) ?></p>
<div style="overflow-x:auto;">
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('MQTT.T_THEMA')) ?></th><th><?= vw_e(vw_t('LOX.T_EINHEIT')) ?></th>
    <th><?= vw_e(vw_t('MQTT.T_BEDEUTUNG')) ?></th></tr>
<?php foreach ($vw_themen as $vw_thema => $vw_info) { ?>
<tr><td><span class="sm-mono"><?= vw_e($vw_cfg['mqtt_topic'] . '/' . $vw_thema) ?></span></td>
    <td><?= empty($vw_info['text']) ? $vw_info['e'] : vw_e(vw_t('MQTT.T_TEXTWERT')) ?></td>
    <td><?= vw_t($vw_info['s']) ?></td></tr>
<?php } ?>
</table>
</div>
<p class="sm-hilfe"><?= vw_t('MQTT.PLATZHALTER') ?></p>
<p class="sm-hilfe"><?= vw_t('MQTT.TEXT_ERKLAERUNG') ?></p>

<h2><?= vw_e(vw_t('MQTT.H_VORLAGE')) ?></h2>
<p class="sm-hilfe"><?= vw_t('MQTT.VORLAGE_ERKLAERUNG') ?></p>
<div class="sm-legende"><span><i class="sm-punkt sm-b-lesen"></i> <?= vw_t('LEGENDE.LESEN') ?></span></div>
<div class="sm-knopfreihe">
<?php foreach (($vw_fahrzeuge ? array_keys($vw_fahrzeuge) : array('1')) as $vw_nr2) { ?>
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-mqtt">
    <?= vw_formfeld($vw_cfg) ?>
    <input data-role="none" type="hidden" name="vorlage_nr" value="<?= (int) $vw_nr2 ?>">
    <button data-role="none" class="sm-btn sm-b-lesen" type="submit" name="vorlage" value="mqtt"><?= vw_e(sprintf(vw_t('MQTT.K_VORLAGE'), (int) $vw_nr2)) ?></button>
  </form>
<?php } ?>
</div>
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
    <?= vw_formfeld($vw_cfg) ?>
    <input data-role="none" type="hidden" name="vorlage_nr" value="1">
    <button data-role="none" class="sm-btn sm-b-lesen" type="submit" name="vorlage" value="status"><?= vw_e(vw_t('LOX.K_VORLAGE')) ?></button>
  </form>
</div>
<div class="sm-legende">
<span><i class="sm-punkt sm-b-lesen"></i> <?= vw_t('LEGENDE.LESEN') ?></span>
</div>
</div>

<?php
/* ---------------- Alle Vorlagen, je Fahrzeug ----------------
 *
 * Bis 0.9.9 gab es genau eine Vorlage: den Status-Endpunkt fuer Fahrzeug 1.
 * Die Feldlisten fuer Laden, Wartung, Position und Verbrauch lagen fertig
 * daneben, und die elf schaltenden Adressen mussten aus einer Tabelle
 * abgetippt werden. */
$vw_nummern = $vw_fahrzeuge ? array_keys($vw_fahrzeuge) : array('1');
?>
<div class="sm-step"><b><?= vw_e(vw_t('LOX.SV_TITEL')) ?></b><br>
<?= vw_t('LOX.SV_TEXT') ?>
<div class="sm-legende">
<span><i class="sm-punkt sm-b-lesen"></i> <?= vw_t('LEGENDE.LESEN') ?></span>
</div>
<?php foreach ($vw_nummern as $vw_nr3) { ?>
<h3><?= vw_e(sprintf(vw_t('LOX.SV_FAHRZEUG'), (int) $vw_nr3)) ?>
<?= isset($vw_fahrzeuge[$vw_nr3]['modell']) && $vw_fahrzeuge[$vw_nr3]['modell'] !== ''
    ? ' &middot; ' . vw_e($vw_fahrzeuge[$vw_nr3]['modell']) : '' ?></h3>
<div class="sm-knopfreihe">
<?php foreach (array_merge(vw_vorlagenarten(), array('befehle')) as $vw_art2) {
    /* Die Ausgangsvorlage nennt die eingreifenden Befehle nur, wenn der
     * zweite Haken gesetzt ist - ein Ausgang auf eine gesperrte Adresse
     * bekaeme 403, und ein Virtueller Ausgang wertet die Antwort nicht aus. */
    ?>
  <form action="index.php" method="post">
    <input data-role="none" type="hidden" name="activetab" value="tab-loxone">
    <?= vw_formfeld($vw_cfg) ?>
    <input data-role="none" type="hidden" name="vorlage_nr" value="<?= (int) $vw_nr3 ?>">
    <button data-role="none" class="sm-btn <?= $vw_art2 === 'befehle' ? 'sm-b-aktion' : 'sm-b-lesen' ?>" type="submit" name="vorlage" value="<?= vw_e($vw_art2) ?>"><?= vw_e(vw_t('LOX.ART_' . strtoupper($vw_art2))) ?></button>
  </form>
<?php } ?>
</div>
<?php } ?>
<div class="sm-legende">
<span><i class="sm-punkt sm-b-lesen"></i> <?= vw_t('LEGENDE.LESEN') ?></span>
<span><i class="sm-punkt sm-b-aktion"></i> <?= vw_t('LEGENDE.AKTION_VORLAGE') ?></span>
</div>
<?php if (empty($vw_cfg['eingreifend_ein'])) { ?>
<div class="sm-hinweis"><?= vw_t('LOX.SV_OHNE_EINGREIFEND') ?></div>
<?php } ?>
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
</table>
<?php
/* Die Tabelle entsteht aus vw_befehle() - derselben Quelle, aus der auch die
 * Positivliste des Endpunkts und die Ausgangsvorlage kommen.
 *
 * Bis 0.9.9 stand sie hier woertlich und nannte acht der elf Befehle;
 * 'zieltemperatur', 'scheibe_aus' und 'wecken' fehlten, und 'zieltemperatur'
 * kam in der ganzen Oberflaeche nicht vor, obwohl der Endpunkt ihn annahm.
 * Eine Liste an zwei Stellen ist eine Stelle zu viel. */
?>
<div style="overflow-x:auto;">
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('LOX.T_BEFEHL')) ?></th><th><?= vw_e(vw_t('LOX.T_ADRESSE')) ?></th>
    <th><?= vw_e(vw_t('LOX.T_BEDEUTUNG')) ?></th></tr>
<?php foreach (vw_befehle() as $vw_bn => $vw_bi) {
    $vw_gesperrt = !empty($vw_bi['eingreifend']) && empty($vw_cfg['eingreifend_ein']);
?>
<tr><td><?= vw_t($vw_bi['s']) ?><?= $vw_gesperrt ? ' <span class="sm-aus">&#9679;</span>' : '' ?></td>
    <td><span class="sm-mono">/plugins/<?= vw_e($vw_p['plugin']) ?>/index.php?token=<?= vw_e($vw_token) ?>&amp;aktion=<?= vw_e($vw_bn) ?><?= $vw_bn === 'abruf' ? '' : '&amp;fahrzeug=1' ?><?= isset($vw_bi['param']) ? str_replace(array('&', '<', '>'), array('&amp;', '&lt;', '&gt;'), $vw_bi['param']) : '' ?></span></td>
    <td><?= vw_t($vw_bi['s'] . '_H') ?></td></tr>
<?php } ?>
</table>
</div>
<p class="sm-hilfe"><span class="sm-aus">&#9679;</span> <?= vw_t('LOX.S5_GESPERRT') ?></p>
<div class="sm-warnung"><?= vw_t('LOX.S5_WARNUNG') ?></div>
</div>

<div class="sm-step"><b><?= vw_e(vw_t('LOX.SS_TITEL')) ?></b><br>
<?= vw_t('LOX.SS_TEXT') ?>
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('LOX.T_BEFEHL')) ?></th><th><?= vw_e(vw_t('LOX.T_ADRESSE')) ?></th></tr>
<?php foreach (vw_schalter() as $vw_sn => $vw_ss) { ?>
<tr><td><?= vw_t($vw_ss) ?></td>
    <td><span class="sm-mono">/plugins/<?= vw_e($vw_p['plugin']) ?>/index.php?token=<?= vw_e($vw_token) ?>&amp;aktion=einstellung&amp;fahrzeug=1&amp;name=<?= vw_e($vw_sn) ?>&amp;wert=&lt;v&gt;</span></td></tr>
<?php } ?>
</table>
<div class="sm-hinweis"><?= vw_t('LOX.SS_UNGEMESSEN') ?></div>
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
    <?= vw_formfeld($vw_cfg) ?>
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

<!-- ================= Reiter: Verlauf ================= -->
<div class="sm-seite<?= $vw_tab === 'tab-verlauf' ? ' sm-active' : '' ?>" id="tab-verlauf">
<h2><?= vw_e(vw_t('VERL.H_TITEL')) ?></h2>
<p class="sm-hilfe"><?= vw_t('VERL.EINLEITUNG') ?></p>
<?php if (!$vw_fahrzeuge) { ?>
<div class="sm-warnung"><?= vw_t('EINST.KEINE_FAHRZEUGE') ?></div>
<?php } else {
    /* Der gewaehlte Tag. Aus $_GET, weil die Reiterleiste ohnehin ueber die
     * Adresse arbeitet - und weil ein Blaettern kein Absenden ist. */
    $vw_tagwahl = isset($_GET['tag']) && is_string($_GET['tag'])
                  && preg_match('/^[0-9]{8}$/', $_GET['tag']) ? (string) $_GET['tag'] : date('Ymd');
    foreach ($vw_fahrzeuge as $vw_nr => $vw_fz) {
        $vw_tage = vw_verlauf_tage((int) $vw_nr, 14);
        $vw_punkte = vw_verlauf_lesen((int) $vw_nr, $vw_tagwahl);
?>
<h3><?= vw_e($vw_fz['modell'] ? $vw_fz['modell'] : vw_t('ALLG.OHNE_NAMEN')) ?>
  (<?= vw_e(vw_t('ALLG.FAHRZEUG')) ?> <?= vw_e($vw_nr) ?>)</h3>
<?php if (!$vw_tage) { ?>
<div class="sm-hinweis"><?= vw_t('VERL.KEINE_TAGE') ?></div>
<?php } else { ?>
<p class="sm-hilfe"><?= vw_e(vw_t('VERL.TAGWAHL')) ?>
<?php foreach ($vw_tage as $vw_t1) {
    $vw_lesbar = substr($vw_t1, 6, 2) . '.' . substr($vw_t1, 4, 2) . '.'; ?>
<a href="index.php?form=verlauf&amp;tag=<?= vw_e($vw_t1) ?>"<?= $vw_t1 === $vw_tagwahl ? ' style="font-weight:700;"' : '' ?>><?= vw_e($vw_lesbar) ?></a>
<?php } ?>
</p>
<?php } ?>
<div><?= vw_soc_svg($vw_punkte) ?></div>
<div class="sm-hilfe"><?= sprintf(vw_t('VERL.MESSPUNKTE'), count($vw_punkte),
    vw_e(substr($vw_tagwahl, 6, 2) . '.' . substr($vw_tagwahl, 4, 2) . '.' . substr($vw_tagwahl, 0, 4))) ?></div>

<?php $vw_ladungen = vw_ladungen_lesen((int) $vw_nr, 25); ?>
<h3><?= vw_e(vw_t('VERL.H_LADUNGEN')) ?></h3>
<?php if (!$vw_ladungen) { ?>
<div class="sm-hinweis"><?= vw_t('VERL.KEINE_LADUNGEN') ?></div>
<?php } else { ?>
<div style="overflow-x:auto;">
<table class="sm-tbl">
<tr><th><?= vw_e(vw_t('VERL.T_BEGINN')) ?></th><th><?= vw_e(vw_t('VERL.T_DAUER')) ?></th>
    <th><?= vw_e(vw_t('VERL.T_VON')) ?></th><th><?= vw_e(vw_t('VERL.T_BIS')) ?></th>
    <th><?= vw_e(vw_t('VERL.T_KWH')) ?></th><th><?= vw_e(vw_t('VERL.T_KM')) ?></th>
    <th><?= vw_e(vw_t('VERL.T_QUELLE')) ?></th></tr>
<?php foreach ($vw_ladungen as $vw_l) {
    $vw_dauer = $vw_l['ende'] > $vw_l['start']
              ? (int) round(($vw_l['ende'] - $vw_l['start']) / 60) : null; ?>
<tr><td><?= vw_e(date('d.m. H:i', $vw_l['start'])) ?></td>
    <td><?= $vw_dauer === null ? '&mdash;' : (int) $vw_dauer . ' min' ?></td>
    <td><?= $vw_l['soc_vor'] === null ? '&mdash;' : vw_e($vw_l['soc_vor']) . ' %' ?></td>
    <td><?= $vw_l['soc_nach'] === null ? '&mdash;' : vw_e($vw_l['soc_nach']) . ' %' ?></td>
    <td><?= $vw_l['kwh'] === null ? '&mdash;' : vw_e($vw_l['kwh']) . ' kWh' ?></td>
    <td><?= $vw_l['km'] === null ? '&mdash;' : vw_e($vw_l['km']) . ' km' ?></td>
    <td><?= vw_e($vw_l['quelle']) ?></td></tr>
<?php } ?>
</table>
</div>
<div class="sm-hilfe"><?= vw_t('VERL.LADUNGEN_HINWEIS') ?></div>
<?php } ?>
<?php if (isset($vw_fz['verbrauch']) && $vw_fz['verbrauch'] !== null) { ?>
<div class="sm-kacheln">
  <div class="sm-kachel"><?= vw_e(vw_t('VERL.K_VERBRAUCH')) ?>
    <b><?= vw_e($vw_fz['verbrauch']) ?></b><span class="sm-hilfe">kWh/100 km</span></div>
<?php if (isset($vw_fz['tag_kwh']) && $vw_fz['tag_kwh'] !== null) { ?>
  <div class="sm-kachel"><?= vw_e(vw_t('VERL.K_TAG')) ?>
    <b><?= vw_e($vw_fz['tag_kwh']) ?></b><span class="sm-hilfe">kWh</span></div>
<?php } ?>
</div>
<div class="sm-hilfe"><?= sprintf(vw_t('VERL.VERBRAUCH_HINWEIS'), 20) ?></div>
<?php } else { ?>
<div class="sm-hinweis"><?= sprintf(vw_t('VERL.KEIN_VERBRAUCH'), 20) ?></div>
<?php } ?>
<?php } /* foreach Fahrzeug */ ?>
<?php } ?>
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
    <?= vw_formfeld($vw_cfg) ?>
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
<?= vw_formfeld($vw_cfg) ?>
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
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="zieltemperatur"><?= vw_e(vw_t('TEST.K_ZIELTEMP')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="wecken"><?= vw_e(vw_t('TEST.K_WECKEN')) ?></button>
</div>

<h3><?= vw_e(vw_t('TEST.H_SCHALTER')) ?></h3>
<p class="sm-hilfe"><?= vw_t('TEST.SCHALTER_ERKLAERUNG') ?></p>
<div class="sm-feld">
  <label for="test_schalter"><?= vw_e(vw_t('TEST.L_SCHALTER')) ?></label>
  <select data-role="none" id="test_schalter" name="test_schalter">
<?php foreach (vw_schalter() as $vw_sn2 => $vw_ss2) { ?>
    <option value="<?= vw_e($vw_sn2) ?>"><?= vw_t($vw_ss2) ?></option>
<?php } ?>
  </select>
</div>
<div class="sm-knopfreihe">
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="einstellung_ein"><?= vw_e(vw_t('TEST.K_SCHALTER_EIN')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="einstellung_aus"><?= vw_e(vw_t('TEST.K_SCHALTER_AUS')) ?></button>
</div>

<h3><?= vw_e(vw_t('TEST.H_EINGREIFEND')) ?></h3>
<div class="sm-warnung"><?= vw_t('TEST.EINGREIFEND_WARNUNG') ?></div>
<?php if (empty($vw_cfg['eingreifend_ein'])) { ?>
<div class="sm-hinweis"><?= vw_t('TEST.EINGREIFEND_GESPERRT') ?></div>
<?php } ?>
<div class="sm-knopfreihe">
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="verriegeln"><?= vw_e(vw_t('TEST.K_VERRIEGELN')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="entriegeln"><?= vw_e(vw_t('TEST.K_ENTRIEGELN')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="blinken"><?= vw_e(vw_t('TEST.K_BLINKEN')) ?></button>
  <button data-role="none" class="sm-btn sm-b-aktion" type="submit" name="test" value="hupen"><?= vw_e(vw_t('TEST.K_HUPEN')) ?></button>
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
    <?= vw_formfeld($vw_cfg) ?>
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
