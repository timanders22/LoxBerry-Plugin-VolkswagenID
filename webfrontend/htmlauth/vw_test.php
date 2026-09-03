<?php
/**
 * Volkswagen ID - die Aktionen des Reiters Test
 *
 * Die Selbstpruefung beantwortet OHNE Loxone und ohne Volkswagen-Konto, ob die
 * Einrichtung traegt. Was sich nur mit Fahrzeug pruefen liesse, wird als
 * solches benannt statt geraten.
 */

/** Eine Zeile der Selbstpruefung. $stand: 1 = ja, 0 = nein, -1 = Hinweis. */
function vw_pruefzeile($stand, $frage, $antwort)
{
    return array('stand' => $stand, 'frage' => $frage, 'antwort' => $antwort);
}

function vw_pruefungen()
{
    $p = vw_paths();
    $cfg = vw_config();
    $z = vw_zugang();
    $zeilen = array();

    $venv = $p['bindir'] . '/venv/bin/python3';
    $zeilen[] = vw_pruefzeile(is_file($venv) ? 1 : 0, vw_t('TEST.F_VENV'),
        is_file($venv) ? $venv : vw_t('TEST.A_VENV_FEHLT'));

    /* Wie lang ist das Aktionstoken?
     *
     * Die Regel in vw_regeln() laesst seit 0.9.12 jede Laenge ab 1 zu -
     * bewusst: ein zu enges Muster verwirft ein gueltiges, von Hand
     * gesetztes Token, und der Schaden ist derselbe wie bei einem
     * verlorenen (Vorfall vom 27.08.2026). Ein kurzes Token ist trotzdem
     * schwach: es steht in jeder Loxone-Adresse und schuetzt das Schalten.
     * Deshalb wird es GEMELDET, nicht abgewiesen. Selbst erzeugte Token
     * haben 24 Zeichen. */
    $vw_tk = trim((string) $cfg['aktionstoken']);
    if ($vw_tk === '') {
        $zeilen[] = vw_pruefzeile(0, vw_t('TEST.F_TOKENLAENGE'),
            vw_t('TEST.A_TOKEN_FEHLT'));
    } elseif (strlen($vw_tk) < 16) {
        $zeilen[] = vw_pruefzeile(0, vw_t('TEST.F_TOKENLAENGE'),
            sprintf(vw_t('TEST.A_TOKEN_KURZ'), strlen($vw_tk)));
    } else {
        $zeilen[] = vw_pruefzeile(1, vw_t('TEST.F_TOKENLAENGE'),
            sprintf(vw_t('TEST.A_TOKEN_OK'), strlen($vw_tk)));
    }

    // carconnectivity verlangt Python 3.9 oder neuer. Das ist auf jedem
    // LoxBerry erfuellt, den es heute gibt (Debian 12 liefert 3.11) - die
    // Zeile bleibt trotzdem stehen, damit man es schwarz auf weiss hat.
    $pyv = vw_python_fassung();
    $pyok = 0;
    if ($pyv !== '') {
        $teile = explode('.', $pyv);
        $pyok = ((int) $teile[0] > 3 || ((int) $teile[0] === 3 && (int) $teile[1] >= 9)) ? 1 : 0;
    }
    $zeilen[] = vw_pruefzeile($pyv === '' ? 0 : $pyok, vw_t('TEST.F_PYTHON'),
        $pyv !== '' ? vw_e($pyv) . ($pyok ? '' : ' &mdash; ' . vw_t('TEST.A_PYTHON_ZU_ALT'))
                    : vw_t('TEST.A_PYTHON_UNBEKANNT'));

    $f = vw_bibliothek_fassungen();
    $zeilen[] = vw_pruefzeile($f['kern'] !== '' ? 1 : 0, vw_t('TEST.F_LIB'),
        $f['kern'] !== '' ? 'carconnectivity ' . vw_e($f['kern']) : vw_t('TEST.A_LIB_FEHLT'));
    $zeilen[] = vw_pruefzeile($f['connector'] !== '' ? 1 : 0, vw_t('TEST.F_CONNECTOR'),
        $f['connector'] !== '' ? 'carconnectivity-connector-volkswagen ' . vw_e($f['connector'])
                               : vw_t('TEST.A_CONNECTOR_FEHLT'));

    $pid = vw_dienst_pid();
    $zeilen[] = vw_pruefzeile($pid > 0 ? 1 : 0, vw_t('TEST.F_DIENST'),
        $pid > 0 ? vw_t('TEST.A_DIENST_LAEUFT') . ' ' . $pid
                 : (vw_dienst_soll() ? vw_t('TEST.A_DIENST_SOLL_TOT') : vw_t('TEST.A_DIENST_GESTOPPT')));

    $zeilen[] = vw_pruefzeile($z['email'] !== '' && strpos($z['email'], '@') !== false ? 1 : 0,
        vw_t('TEST.F_KONTO'),
        $z['email'] !== '' ? vw_e($z['email']) : vw_t('TEST.A_KONTO_FEHLT'));

    // Ein Pruefknopf darf die FORM eines Geheimnisses beurteilen, nie seinen
    // Wert anzeigen.
    $zeilen[] = vw_pruefzeile($z['laenge'] > 0 ? 1 : 0, vw_t('TEST.F_PASSWORT'),
        $z['laenge'] > 0 ? sprintf(vw_t('TEST.A_PASSWORT_DA'), $z['laenge']) : vw_t('TEST.A_PASSWORT_FEHLT'));

    $rechte = is_file($p['zugang']) ? (fileperms($p['zugang']) & 0777) : -1;
    $zeilen[] = vw_pruefzeile(($rechte >= 0 && ($rechte & 0077) === 0) ? 1 : 0,
        vw_t('TEST.F_RECHTE'),
        $rechte >= 0 ? '0' . decoct($rechte) : vw_t('TEST.A_ZUGANGSDATEI_FEHLT'));

    // In der Markendatei der Bibliothek stehen Anmeldemarken. Sie darf
    // niemandem sonst lesbar sein; die Bibliothek setzt die Rechte nicht
    // selbst, der Dienst holt es nach.
    $marke = $p['datadir'] . '/token.json';
    if (is_file($marke)) {
        $mr = fileperms($marke) & 0777;
        $zeilen[] = vw_pruefzeile(($mr & 0077) === 0 ? 1 : 0, vw_t('TEST.F_MARKE'),
            '0' . decoct($mr));
    } else {
        $zeilen[] = vw_pruefzeile(-1, vw_t('TEST.F_MARKE'), vw_t('TEST.A_MARKE_FEHLT'));
    }

    $fahrzeuge = vw_fahrzeuge();
    $zeilen[] = vw_pruefzeile(count($fahrzeuge) > 0 ? 1 : 0, vw_t('TEST.F_FAHRZEUGE'),
        count($fahrzeuge) > 0 ? sprintf(vw_t('TEST.A_FAHRZEUGE'), count($fahrzeuge))
                              : vw_t('TEST.A_KEINE_FAHRZEUGE'));

    // Ausgefallene Einzelabrufe benennen, statt sie zu verschweigen. Ein
    // Fahrzeug, das die Klimasteuerung nicht kennt, ist kein Fehler - ein
    // stillschweigend leeres Feld dagegen schon.
    $aus = array();
    foreach ($fahrzeuge as $nr => $f) {
        if (!empty($f['ausfaelle']) && is_array($f['ausfaelle'])) {
            foreach (array_keys($f['ausfaelle']) as $name) {
                $aus[] = $nr . ':' . $name;
            }
        }
    }
    if ($aus) {
        $zeilen[] = vw_pruefzeile(0, vw_t('TEST.F_AUSFAELLE'), vw_e(implode(', ', $aus)));
    } elseif ($fahrzeuge) {
        $zeilen[] = vw_pruefzeile(1, vw_t('TEST.F_AUSFAELLE'), vw_t('TEST.A_KEINE_AUSFAELLE'));
    }

    $alter = vw_alter();
    if ($alter < 0) {
        $zeilen[] = vw_pruefzeile(0, vw_t('TEST.F_ABRUF'), vw_t('TEST.A_NIE_ABGERUFEN'));
    } else {
        $frisch = $alter <= max(600, 3 * (int) $cfg['intervall']);
        $zeilen[] = vw_pruefzeile($frisch ? 1 : 0, vw_t('TEST.F_ABRUF'),
            sprintf(vw_t('TEST.A_ABRUF_ALTER'), $alter));
    }

    $zu = vw_zustand();
    if (!empty($zu['fehler'])) {
        $zeilen[] = vw_pruefzeile(0, vw_t('TEST.F_LETZTER_FEHLER'), vw_e($zu['fehler']));
    }

    $m = vw_mqtt_zustand();
    if (!$m['gefunden']) {
        $zeilen[] = vw_pruefzeile(0, vw_t('TEST.F_MQTT'), vw_t('TEST.A_MQTT_NICHT_GEFUNDEN'));
    } elseif ($m['autostart']) {
        $zeilen[] = vw_pruefzeile(1, vw_t('TEST.F_MQTT'),
            vw_e($m['broker']) . ':' . vw_e($m['brokerport']) . ' (UDP ' . (int) $m['udpport'] . ')');
    } else {
        $zeilen[] = vw_pruefzeile(0, vw_t('TEST.F_MQTT'), vw_t('TEST.A_MQTT_AUS'));
    }

    /* ---- Reiter: Liste, Beschriftungen und Bereiche ----
     * Der Fall, den kein Werkzeug dieses Hauses hier gesehen hat: die Leiste
     * entsteht in einer Schleife, und hausstandard_pruefen.py sucht
     * woertliche Namen im Quelltext - es meldete einen Strich, also "nichts
     * gemessen", was sich wie "nichts zu beanstanden" liest. */
    $r = vw_reiter_lesen();
    if ($r === null || !$r['liste'] || !$r['bereiche']) {
        $zeilen[] = vw_pruefzeile(-1, vw_t('TEST.F_REITER'),
            vw_t('TEST.A_REITER_UNKLAR'));
    } else {
        $abw = array();
        $ohne_bereich = array_values(array_diff($r['liste'], $r['bereiche']));
        if ($ohne_bereich) {
            $abw[] = sprintf(vw_t('TEST.A_REITER_OHNE_BEREICH'),
                vw_e(implode(', ', $ohne_bereich)));
        }
        $ohne_liste = array_values(array_diff($r['bereiche'], $r['liste']));
        if ($ohne_liste) {
            $abw[] = sprintf(vw_t('TEST.A_REITER_OHNE_LISTE'),
                vw_e(implode(', ', $ohne_liste)));
        }
        $ohne_text = array_values(array_diff($r['liste'], $r['text']));
        if ($ohne_text) {
            $abw[] = sprintf(vw_t('TEST.A_REITER_OHNE_TEXT'),
                vw_e(implode(', ', $ohne_text)));
        }
        // Ein Reiter, den die Leiste nicht zeigt: er ist unerreichbar.
        $ohne_leiste = array_values(array_diff($r['liste'], $r['leiste_fest']));
        if ($ohne_leiste) {
            $abw[] = sprintf(vw_t('TEST.A_REITER_OHNE_LEISTE'),
                vw_e(implode(', ', $ohne_leiste)));
        }
        // Ein woertlicher Eintrag in der Leiste, den die Liste nicht kennt.
        // Heute gibt es keinen - aber wer die Schleife spaeter aufloest,
        // soll es hier erfahren und nicht am Bildschirm.
        $leiste_fremd = array_values(array_diff($r['leiste_fest'], $r['liste']));
        if ($leiste_fremd) {
            $abw[] = sprintf(vw_t('TEST.A_REITER_LEISTE_FEST'),
                vw_e(implode(', ', $leiste_fremd)));
        }
        $zeilen[] = vw_pruefzeile($abw ? 0 : 1, vw_t('TEST.F_REITER'),
            $abw ? sprintf(vw_t('TEST.A_REITER_FEHL'), implode(' ', $abw))
                 : sprintf(vw_t('TEST.A_REITER_OK'), count($r['liste'])));
    }

    $zeilen[] = vw_pruefzeile(!empty($cfg['steuerung_ein']) ? 1 : -1, vw_t('TEST.F_STEUERUNG'),
        !empty($cfg['steuerung_ein']) ? vw_t('TEST.A_STEUERUNG_EIN') : vw_t('TEST.A_STEUERUNG_AUS'));
    $zeilen[] = vw_pruefzeile(!empty($cfg['eingreifend_ein']) ? 1 : -1,
        vw_t('TEST.F_EINGREIFEND'),
        !empty($cfg['eingreifend_ein']) ? vw_t('TEST.A_EINGREIFEND_EIN')
                                        : vw_t('TEST.A_EINGREIFEND_AUS'));

    /* ---- ab 0.9.10: die Zeilen des Hausstandards ---- */

    // Arbeitet der Dienst noch? Eine Prozessnummer beantwortet das nicht -
    // ein Prozess kann dastehen und nichts mehr tun. Der Zaehler laeuft bei
    // jedem Takt eine Stelle weiter, auch bei einer Stoerung.
    $zu2 = vw_zustand();
    $zzeit = isset($zu2['ts']) && is_numeric($zu2['ts']) ? (int) $zu2['ts'] : 0;
    if ($pid <= 0) {
        // Ueber einen Dienst, der gar nicht laeuft, wird kein Herzschlag beurteilt.
        $zeilen[] = vw_pruefzeile(-1, vw_t('TEST.F_HERZ'), vw_t('TEST.A_HERZ_KEIN_DIENST'));
    } elseif ($zzeit <= 0) {
        $zeilen[] = vw_pruefzeile(-1, vw_t('TEST.F_HERZ'), vw_t('TEST.A_HERZ_UNBEKANNT'));
    } else {
        $alt = time() - $zzeit;
        $frisch2 = $alt <= max(600, 3 * (int) $cfg['intervall']);
        $zeilen[] = vw_pruefzeile($frisch2 ? 1 : 0, vw_t('TEST.F_HERZ'),
            sprintf(vw_t('TEST.A_HERZ'), $alt,
                    isset($zu2['zaehler']) ? (int) $zu2['zaehler'] : -1));
    }

    // Ist die Konfiguration heil? Jeder Zustand, den der Code erzeugen kann,
    // braucht seinen Satz.
    $lage = vw_config_lesen(true);
    $mangel = array();
    if ($lage['abgewiesen']) {
        $mangel[] = sprintf(vw_t('TEST.A_KONFIG_ABGEWIESEN'),
            vw_e(implode(', ', array_keys($lage['abgewiesen']))));
    }
    if ($lage['fremd']) {
        $mangel[] = sprintf(vw_t('TEST.A_KONFIG_FREMD'), vw_e(implode(', ', $lage['fremd'])));
    }
    $zeilen[] = vw_pruefzeile(($lage['lage'] === 'ok' && !$mangel) ? 1 : 0,
        vw_t('TEST.F_KONFIG'),
        vw_t('ALLG.KONFIG_' . strtoupper($lage['lage'])) . ($mangel ? ' ' . implode(' ', $mangel) : ''));

    // Antwortet der eigene Endpunkt? Ein echter Aufruf auf 127.0.0.1 - er
    // findet die getrennten Baeume, die keine Lesepruefung sieht.
    $zeilen[] = vw_endpunkt_zeile($cfg);

    // Tragen alle Formulare das Merkmal? Ein Formular vergisst man. Gemeldet
    // wird die ZAHL der angesehenen Stellen: eine Null ist kein "in Ordnung",
    // sondern ein Hinweis, dass nichts gemessen wurde.
    $f = vw_formulare_lesen();
    if ($f === null || $f['formulare'] === 0) {
        $zeilen[] = vw_pruefzeile(-1, vw_t('TEST.F_MERKMAL'), vw_t('TEST.A_MERKMAL_UNKLAR'));
    } else {
        $zeilen[] = vw_pruefzeile($f['ohne'] === 0 ? 1 : 0, vw_t('TEST.F_MERKMAL'),
            $f['ohne'] === 0 ? sprintf(vw_t('TEST.A_MERKMAL_OK'), $f['formulare'])
                             : sprintf(vw_t('TEST.A_MERKMAL_FEHLT'), $f['ohne'], $f['formulare']));
    }

    // Stimmt die Themenliste mit dem Sendecode ueberein? Die Tabelle im Reiter
    // MQTT ist die Anleitung - eine Liste, die niemand nachmisst, laeuft
    // auseinander.
    $zeilen[] = vw_themen_zeile();

    // Sind die Vorlagen wohlgeformt? Eine kaputte Vorlage merkt der Anwender
    // sonst erst in Loxone Config.
    $zeilen[] = vw_vorlagen_zeile();

    // Laufen die beiden Entfernungsrechnungen (PHP und Python) gleich?
    // Muenchen - Berlin sind rund 504 km.
    $e = vw_entfernung_m(48.1372, 11.5756, 52.5200, 13.4050);
    $zeilen[] = vw_pruefzeile(($e !== null && $e >= 500000 && $e <= 508000) ? 1 : 0,
        vw_t('TEST.F_ENTFERNUNG'),
        sprintf(vw_t('TEST.A_ENTFERNUNG'), $e === null ? '-' : $e));

    // Der Horcher - nur wenn er gebraucht wird.
    if ($cfg['empf_thema'] !== '' || (!empty($cfg['abfahrt_ein']) && $cfg['abfahrt_thema'] !== '')) {
        $h = isset($zu2['horcher']) ? (int) $zu2['horcher'] : -1;
        $zeilen[] = vw_pruefzeile($h === 1 ? 1 : ($h === 0 ? 0 : -1), vw_t('TEST.F_HORCHER'),
            $h === 1 ? vw_t('TEST.A_HORCHER_OK')
                     : (!empty($zu2['horcher_grund']) ? vw_e($zu2['horcher_grund'])
                                                      : vw_t('TEST.A_HORCHER_UNBEKANNT')));
    }

    // Sind die Schluessel da, die erst zur Laufzeit entstehen?
    $zeilen[] = vw_dynamische_schluessel_zeile();

    // Zum Schluss: wie viele Striche stehen in dieser Liste? Ein Strich ist
    // ausdruecklich kein Haken - wer ihn beim Ueberfliegen wie einen
    // einsammelt, hat eine Pruefung weniger, als er glaubt.
    $striche = 0;
    foreach ($zeilen as $z2) {
        if ($z2['stand'] === -1) {
            $striche++;
        }
    }
    $zeilen[] = vw_pruefzeile(-1, vw_t('TEST.F_BILANZ'),
        sprintf(vw_t('TEST.A_BILANZ'), count($zeilen), $striche));

    return $zeilen;
}

/**
 * Die Schluessel, die erst zur Laufzeit aus einer Vorsilbe entstehen.
 *
 * Sie stehen hier WOERTLICH, und das ist der ganze Zweck: ein Sucher nach
 * vw_t('X') findet einen zusammengesetzten Aufruf wie
 * vw_t('LOX.ART_' . strtoupper($art)) nicht und meldet die Schluessel als
 * unbenutzt. Wer sie daraufhin loescht, bekommt eine Seite, auf der der
 * Schluesselname selbst steht.
 *
 * Und es ist keine Attrappe fuer den Sucher: die Zeile im Reiter Test ruft
 * jeden Schluessel wirklich ab. vw_t() gibt bei einem unbekannten Schluessel
 * den Namen zurueck - daran ist ein fehlender Text zu erkennen.
 */
function vw_dynamische_schluessel()
{
    /* AUSGESCHRIEBEN, nicht erzeugt. Ein Sucher findet nur woertliche Namen;
     * eine Schleife ueber vw_befehle() haette dieselben Schluessel geliefert
     * und trotzdem als unbenutzt gemeldet. */
    return array(
        'ALLG.KONFIG_OK', 'ALLG.KONFIG_LEER', 'ALLG.KONFIG_KAPUTT',
        'ALLG.KONFIG_AUS_ZWEITSCHRIFT',
        'EINST.DIENST_START', 'EINST.DIENST_STOP', 'EINST.DIENST_RESTART',
        'LOX.ART_STATUS', 'LOX.ART_LADEN', 'LOX.ART_WARTUNG', 'LOX.ART_POSITION',
        'LOX.ART_VERBRAUCH', 'LOX.ART_BEFEHLE',
        'VW_BEF.KLIMA_START_H', 'VW_BEF.KLIMA_STOP_H', 'VW_BEF.ZIELTEMP_H',
        'VW_BEF.LADEN_START_H', 'VW_BEF.LADEN_STOP_H', 'VW_BEF.LADEGRENZE_H',
        'VW_BEF.LADESTROM_H', 'VW_BEF.SCHEIBE_EIN_H', 'VW_BEF.SCHEIBE_AUS_H',
        'VW_BEF.WECKEN_H', 'VW_BEF.ABRUF_H', 'VW_BEF.ENTRIEGELN_H',
        'VW_BEF.VERRIEGELN_H', 'VW_BEF.BLINKEN_H', 'VW_BEF.HUPEN_H',
        'VW_BEF.EINSTELLUNG_H',
        'EINST.L_INTERVALL', 'EINST.L_TAKT_WARTUNG', 'EINST.L_TEMP_MIN',
        'EINST.L_TEMP_MAX', 'EINST.L_VERLAUF_TAGE', 'EINST.L_WARTEZEIT',
        'EINST.L_WARTEZEIT_ENDPUNKT', 'EINST.L_HEIM_RADIUS', 'EINST.L_ABSTAND_ABRUF',
        'EINST.L_BEFEHLE_STUNDE', 'EINST.L_ENTPRELLUNG', 'EINST.L_ABFAHRT_VORLAUF',
        'EINST.L_ABFAHRT_TEMP', 'EINST.L_HEIM_BREITE', 'EINST.L_HEIM_LAENGE',
        'EINST.L_EMPF_GRENZE', 'EINST.L_EMPF_THEMA', 'EINST.L_ABFAHRT_THEMA',
    );
}

/**
 * Gegenprobe: deckt die ausgeschriebene Liste wirklich ab, was der Code
 * zusammensetzt?
 *
 * Ohne diese Probe waere die Liste oben eine Behauptung. Wer einen Befehl
 * ergaenzt und den Hilfstext vergisst, erfaehrt es hier - und nicht erst,
 * wenn der Anwender in der Tabelle einen Schluesselnamen liest.
 */
function vw_dynamische_schluessel_soll()
{
    $soll = array();
    foreach (array_merge(vw_vorlagenarten(), array('befehle')) as $a) {
        $soll[] = 'LOX.ART_' . strtoupper($a);
    }
    foreach (array_keys(vw_befehle()) as $b) {
        $soll[] = 'VW_BEF.' . strtoupper($b) . '_H';
    }
    return $soll;
}

function vw_dynamische_schluessel_zeile()
{
    $alle = vw_dynamische_schluessel();
    $fehlt = array();
    foreach ($alle as $k) {
        // vw_t() gibt den Schluessel selbst zurueck, wenn es ihn nicht gibt.
        if (vw_t($k) === $k) {
            $fehlt[] = $k;
        }
    }
    // Und was der Code bildet, ohne dass es in der Liste steht.
    $ungenannt = array_values(array_diff(vw_dynamische_schluessel_soll(), $alle));
    foreach ($ungenannt as $k) {
        if (!in_array($k, $fehlt, true)) {
            $fehlt[] = $k;
        }
    }
    return vw_pruefzeile($fehlt ? 0 : 1, vw_t('TEST.F_DYNAMISCH'),
        $fehlt ? sprintf(vw_t('TEST.A_DYNAMISCH_FEHLT'), count($fehlt),
                         vw_e(implode(', ', array_slice($fehlt, 0, 8))))
               : sprintf(vw_t('TEST.A_DYNAMISCH_OK'), count($alle)));
}

/**
 * Ein echter HTTP-Aufruf gegen den eigenen Endpunkt.
 *
 * DREI Ausgaenge, nicht zwei. Der dritte ist der wichtige: ein Webserver, der
 * nur eine Anfrage zugleich bearbeitet, kann sich waehrend des Seitenaufbaus
 * nicht selbst aufrufen - ein Kreuz waere dort ein Kreuz, das nichts bedeutet.
 *
 * Das Ergebnis wird 300 Sekunden zwischengespeichert, sonst ruft sich der
 * Webserver bei jedem Klick auf einen Reiter selbst auf.
 */
function vw_endpunkt_zeile($cfg)
{
    $p = vw_paths();
    $speicher = $p['datadir'] . '/.endpunkt_probe.json';
    $alt = vw_json_lesen($speicher);
    if (isset($alt['ts']) && (time() - (int) $alt['ts']) < 300 && isset($alt['stand'])) {
        return vw_pruefzeile((int) $alt['stand'], vw_t('TEST.F_ENDPUNKT'), (string) $alt['text']);
    }
    $token = trim((string) $cfg['aktionstoken']);
    if ($token === '') {
        return vw_pruefzeile(-1, vw_t('TEST.F_ENDPUNKT'), vw_t('TEST.A_ENDPUNKT_KEIN_TOKEN'));
    }
    $url = 'http://127.0.0.1/plugins/' . rawurlencode($p['plugin'])
         . '/index.php?token=' . rawurlencode($token) . '&aktion=status&fahrzeug=1';
    $kontext = stream_context_create(array('http' => array(
        'timeout' => 4, 'ignore_errors' => true, 'method' => 'GET')));
    /* Ein EIGENER Fehler-Aufnehmer um den Aufruf.
     *
     * Ein vorangestelltes @ schaltet nur die AUSGABE ab, nicht den
     * Fehler-Aufnehmer: eine verweigerte Verbindung - der Normalfall auf einem
     * Webserver, der sich nicht selbst aufrufen kann - landete damit in jedem
     * Fehlerprotokoll und in jedem Renderlauf des Pruefstands. Die verweigerte
     * Verbindung ist hier aber ein gueltiges MESSERGEBNIS, kein Fehler; sie
     * fuehrt zum dritten Ausgang. */
    $vorher = set_error_handler(function () { return true; });
    $antwort = file_get_contents($url, false, $kontext);
    $kopf = isset($http_response_header) ? $http_response_header : null;
    if ($vorher === null) {
        restore_error_handler();
    } else {
        set_error_handler($vorher);
    }
    $code = 0;
    if (is_array($kopf) && isset($kopf[0])
        && preg_match('#HTTP/\S+\s+(\d{3})#', (string) $kopf[0], $m)) {
        $code = (int) $m[1];
    }
    if ($antwort === false || $antwort === '') {
        $stand = -1;
        $text = vw_t('TEST.A_ENDPUNKT_KEINE_ANTWORT');
    } elseif ($code === 200 && strpos($antwort, 'VOLKSWAGEN;') === 0) {
        $stand = 1;
        $text = sprintf(vw_t('TEST.A_ENDPUNKT_OK'), $code);
    } else {
        $stand = 0;
        $text = sprintf(vw_t('TEST.A_ENDPUNKT_FALSCH'), $code,
            vw_e(substr(trim((string) $antwort), 0, 60)));
    }
    @file_put_contents($speicher, json_encode(
        array('ts' => time(), 'stand' => $stand, 'text' => $text)));
    return vw_pruefzeile($stand, vw_t('TEST.F_ENDPUNKT'), $text);
}

/**
 * Zaehlt die Formulare der Oberflaeche und die darin gefuehrten Merkmale.
 *
 * Gelesen wird der QUELLTEXT, nicht der Zustand zur Laufzeit - nur so faellt
 * ein Formular auf, das es in der Datei gibt und das zur Laufzeit niemand
 * oeffnet.
 */
function vw_formulare_lesen()
{
    $f = __DIR__ . '/index.php';
    if (!is_file($f)) {
        return null;
    }
    $t = (string) @file_get_contents($f);
    if (!preg_match_all('#<form\b.*?</form>#s', $t, $m)) {
        return array('formulare' => 0, 'ohne' => 0);
    }
    $ohne = 0;
    foreach ($m[0] as $block) {
        // Gesucht wird der AUFRUF, der das Feld erzeugt - und dass er einen
        // Wert traegt. Ein woertlich kopiertes Feld mit leerem value hat bei
        // AudiConnect als Rohtext in der Seite gestanden.
        if (strpos($block, 'vw_formfeld(') === false) {
            $ohne++;
        }
    }
    return array('formulare' => count($m[0]), 'ohne' => $ohne);
}

/**
 * Sendet der Dienst genau die Themen, die die Oberflaeche verspricht?
 *
 * Der teuerste Befund der Renault-Sitzung: Oberflaeche, Baustein-Liste und
 * erzeugte Importdatei nannten fuenf Themen, die der Sendecode nie
 * veroeffentlicht hat. Wer die Datei einlas, bekam virtuelle Eingaenge, die
 * dauerhaft auf 0 standen - ohne Fehlermeldung.
 */
function vw_themen_zeile()
{
    $dienst = vw_mqtt_themen_im_dienst();
    if ($dienst === null) {
        return vw_pruefzeile(-1, vw_t('TEST.F_THEMEN'), vw_t('TEST.A_THEMEN_UNKLAR'));
    }
    $soll_zahl = array();
    $soll_text = array();
    $soll_oben = array();
    foreach (vw_mqtt_themen() as $thema => $info) {
        if (strpos($thema, 'fahrzeugN/') === 0) {
            $name = substr($thema, 10);
            if (!empty($info['text'])) {
                $soll_text[] = $name;
            } else {
                $soll_zahl[] = $name;
            }
        } else {
            $soll_oben[] = $thema;
        }
    }
    $mangel = array();
    foreach (array(array('Zahlen', $soll_zahl, $dienst['felder']),
                   array('Texte', $soll_text, $dienst['text']),
                   array('oben', $soll_oben, $dienst['oben'])) as $paar) {
        $nur_liste = array_values(array_diff($paar[1], $paar[2]));
        $nur_dienst = array_values(array_diff($paar[2], $paar[1]));
        if ($nur_liste) {
            $mangel[] = sprintf(vw_t('TEST.A_THEMEN_NUR_LISTE'), $paar[0],
                vw_e(implode(', ', $nur_liste)));
        }
        if ($nur_dienst) {
            $mangel[] = sprintf(vw_t('TEST.A_THEMEN_NUR_DIENST'), $paar[0],
                vw_e(implode(', ', $nur_dienst)));
        }
    }
    $n = count($soll_zahl) + count($soll_text) + count($soll_oben);
    return vw_pruefzeile($mangel ? 0 : 1, vw_t('TEST.F_THEMEN'),
        $mangel ? implode(' ', $mangel) : sprintf(vw_t('TEST.A_THEMEN_OK'), $n));
}

/**
 * Sind alle erzeugbaren Vorlagen wohlgeformt?
 *
 * Geprueft wird das ERZEUGNIS, nicht der Quelltext: jede Vorlage wird gebaut
 * und durch den XML-Parser geschickt. Dazu die drei Merkmale des
 * Hausstandards, die bis 0.9.9 fehlten.
 */
function vw_vorlagen_zeile()
{
    $mangel = array();
    $anzahl = 0;
    $frueher = libxml_use_internal_errors(true);
    foreach (array_merge(vw_vorlagenarten(), array('befehle', 'mqtt')) as $art) {
        if ($art === 'befehle') {
            list($name, $xml) = vw_vorlage_vo(1);
        } elseif ($art === 'mqtt') {
            list($name, $xml) = vw_vorlage_mqtt(1);
        } else {
            list($name, $xml) = vw_vorlage(1, $art);
        }
        $anzahl++;
        libxml_clear_errors();
        $doc = simplexml_load_string($xml);
        if ($doc === false) {
            $e = libxml_get_errors();
            $mangel[] = sprintf(vw_t('TEST.A_VORLAGE_KAPUTT'), vw_e($art),
                vw_e($e ? trim($e[0]->message) : '?'));
            continue;
        }
        if (strpos($xml, '<Info templateType=') === false) {
            $mangel[] = sprintf(vw_t('TEST.A_VORLAGE_OHNE_INFO'), vw_e($art));
        }
        if (strpos($xml, 'HintText=""') === false) {
            $mangel[] = sprintf(vw_t('TEST.A_VORLAGE_OHNE_HINT'), vw_e($art));
        }
        // CRLF ueberall, keine nackte LF. Loxone Config nimmt beides an, der
        // Abgleich gegen die Ausfuhren der Anlage aber nicht.
        if (preg_match('/(?<!\r)\n/', $xml)) {
            $mangel[] = sprintf(vw_t('TEST.A_VORLAGE_LF'), vw_e($art));
        }
    }
    libxml_clear_errors();
    libxml_use_internal_errors($frueher);
    return vw_pruefzeile($mangel ? 0 : 1, vw_t('TEST.F_VORLAGEN'),
        $mangel ? implode(' ', $mangel) : sprintf(vw_t('TEST.A_VORLAGEN_OK'), $anzahl));
}

/**
 * Welche Reiternamen kennen Liste, Beschriftungstabelle und Bereiche?
 *
 * Gelesen wird der QUELLTEXT der Oberflaeche, nicht der Zustand zur Laufzeit.
 * Nur so faellt ein Bereich auf, den es in der Datei gibt und den zur Laufzeit
 * niemand oeffnet.
 *
 * Die Reiterleiste WIRD verglichen, seit sie ausgeschrieben ist (0.9.12).
 * Vorher entstand sie in einer Schleife ueber dieselbe Liste und konnte
 * nicht abweichen - dafuer war sie fuer jedes Werkzeug unsichtbar, das
 * woertliche Namen sucht, und diese Pruefung mass nur eine Richtung.
 * Jetzt sind es beide: ein Eintrag in der Leiste, den die Liste nicht kennt,
 * UND ein Reiter der Liste, den die Leiste nicht zeigt - der waere
 * unerreichbar, und genau dieser Fall ist im Haus schon einmal gruen
 * durchgelaufen.
 */
function vw_reiter_lesen()
{
    $f = __DIR__ . '/index.php';
    if (!is_file($f)) {
        return null;
    }
    $t = (string) @file_get_contents($f);
    $aus = array('liste' => array(), 'text' => array(),
                 'bereiche' => array(), 'leiste_fest' => array());
    if (preg_match('/\$vw_reiter_ids\s*=\s*array\((.*?)\);/s', $t, $m)) {
        preg_match_all("/'([a-z0-9]+)'/", $m[1], $x);
        $aus['liste'] = $x[1];
    }
    if (preg_match('/\$vw_beschriftung\s*=\s*array\((.*?)\);/s', $t, $m)) {
        preg_match_all("/'([a-z0-9]+)'\s*=>/", $m[1], $x);
        $aus['text'] = $x[1];
    }
    preg_match_all('/id="tab-([a-z0-9]+)"/', $t, $z);
    $aus['bereiche'] = $z[1];
    preg_match_all('/data-ziel="tab-([a-z0-9]+)"/', $t, $y);
    $aus['leiste_fest'] = $y[1];
    return $aus;
}

/**
 * Fuehrt eine Aktion des Reiters Test aus.
 * Rueckgabe: array(stand, Meldung) - stand wie bei vw_befehl_absetzen.
 */
function vw_test_aktion($aktion)
{
    $nr = isset($_POST['test_fahrzeug']) ? (string) $_POST['test_fahrzeug'] : '1';
    if (!preg_match('/^[0-9]{1,2}$/', $nr)) {
        return array(0, vw_t('TEST.M_FAHRZEUG_UNGUELTIG'));
    }

    switch ($aktion) {
        case 'abruf':
            return vw_befehl_absetzen(array('aktion' => 'abruf'), 10);

        case 'klima_start':
            $temp = isset($_POST['test_temp']) ? str_replace(',', '.', (string) $_POST['test_temp']) : '';
            if (!preg_match('/^[0-9]{1,2}(\.[05])?$/', $temp)) {
                return array(0, vw_t('TEST.M_TEMP_UNGUELTIG'));
            }
            return vw_befehl_absetzen(array('aktion' => 'klima_start', 'fahrzeug' => $nr, 'temp' => $temp));

        case 'klima_stop':
            return vw_befehl_absetzen(array('aktion' => 'klima_stop', 'fahrzeug' => $nr));

        case 'laden_start':
            return vw_befehl_absetzen(array('aktion' => 'laden_start', 'fahrzeug' => $nr));

        case 'laden_stop':
            return vw_befehl_absetzen(array('aktion' => 'laden_stop', 'fahrzeug' => $nr));

        case 'ladegrenze':
            $p = isset($_POST['test_prozent']) ? (string) $_POST['test_prozent'] : '';
            if (!preg_match('/^[0-9]{1,3}$/', $p)) {
                return array(0, vw_t('TEST.M_PROZENT_UNGUELTIG'));
            }
            return vw_befehl_absetzen(array('aktion' => 'ladegrenze', 'fahrzeug' => $nr, 'prozent' => (int) $p));

        case 'ladestrom':
            $a = isset($_POST['test_ampere']) ? (string) $_POST['test_ampere'] : '';
            if (!preg_match('/^[0-9]{1,2}$/', $a)) {
                return array(0, vw_t('TEST.M_AMPERE_UNGUELTIG'));
            }
            return vw_befehl_absetzen(array('aktion' => 'ladestrom', 'fahrzeug' => $nr, 'ampere' => (int) $a));

        case 'scheibe_ein':
            return vw_befehl_absetzen(array('aktion' => 'scheibe_ein', 'fahrzeug' => $nr));

        case 'scheibe_aus':
            return vw_befehl_absetzen(array('aktion' => 'scheibe_aus', 'fahrzeug' => $nr));

        case 'wecken':
            return vw_befehl_absetzen(array('aktion' => 'wecken', 'fahrzeug' => $nr));

        case 'zieltemperatur':
            $temp = isset($_POST['test_temp']) ? str_replace(',', '.', (string) $_POST['test_temp']) : '';
            if (!preg_match('/^[0-9]{1,2}(\.[05])?$/', $temp)) {
                return array(0, vw_t('TEST.M_TEMP_UNGUELTIG'));
            }
            return vw_befehl_absetzen(array('aktion' => 'zieltemperatur',
                                            'fahrzeug' => $nr, 'temp' => $temp));

        case 'verriegeln':
        case 'entriegeln':
        case 'blinken':
        case 'hupen':
            return vw_befehl_absetzen(array('aktion' => $aktion, 'fahrzeug' => $nr));

        case 'einstellung_ein':
        case 'einstellung_aus':
            $name = isset($_POST['test_schalter']) && is_string($_POST['test_schalter'])
                  ? (string) $_POST['test_schalter'] : '';
            if (!isset(vw_schalter()[$name])) {
                return array(0, vw_t('TEST.M_SCHALTER_UNGUELTIG'));
            }
            return vw_befehl_absetzen(array('aktion' => 'einstellung', 'fahrzeug' => $nr,
                'name' => $name, 'wert' => $aktion === 'einstellung_ein' ? 1 : 0));

        default:
            return array(0, vw_t('TEST.M_UNBEKANNT'));
    }
}

/**
 * Mini-SVG: Fuellstand ueber EINEN Tag (0 bis 24 h, 0 bis 100 %).
 *
 * $tag ist 'YYYYMMDD'; ohne Angabe gilt heute.
 *
 * Bis 0.9.11 kannte die Funktion nur 'today 00:00'. Die Tagwahl kam in
 * 0.9.10 dazu, die Zeitachse wurde nicht mitgezogen: fuer jeden
 * zurueckliegenden Tag lagen alle Zeitstempel vor dem Nullpunkt, jeder Punkt
 * fiel durch die Bereichspruefung, und die Grafik zeigte "noch keine
 * Messpunkte fuer heute" - direkt ueber der Zeile "288 Messpunkte am
 * 01.09.2026". Zwei einander widersprechende Aussagen auf einem Bildschirm.
 */
function vw_soc_svg($punkte, $tag = null)
{
    $w = 720; $h = 120; $x0 = 34; $y0 = 8; $pw = $w - $x0 - 8; $ph = $h - $y0 - 20;
    $tag0 = ($tag !== null && preg_match('/^[0-9]{8}$/', (string) $tag))
          ? (int) mktime(0, 0, 0, (int) substr($tag, 4, 2), (int) substr($tag, 6, 2),
                         (int) substr($tag, 0, 4))
          : strtotime('today 00:00');
    $heute = ($tag === null || (string) $tag === date('Ymd'));
    $svg = '<svg viewBox="0 0 ' . $w . ' ' . $h . '" style="width:100%;max-width:' . $w
         . 'px;height:auto;background:#fafafa;border:1px solid #e0e0e0;border-radius:8px;"'
         . ' xmlns="http://www.w3.org/2000/svg">';
    foreach (array(0, 25, 50, 75, 100) as $pct) {
        $y = $y0 + $ph - $ph * $pct / 100;
        $svg .= '<line x1="' . $x0 . '" y1="' . $y . '" x2="' . ($x0 + $pw) . '" y2="' . $y
              . '" stroke="#e5e5e5" stroke-width="1"/>';
        $svg .= '<text x="' . ($x0 - 5) . '" y="' . ($y + 3)
              . '" font-size="9" fill="#999" text-anchor="end">' . $pct . '</text>';
    }
    foreach (array(0, 6, 12, 18, 24) as $hh) {
        $x = $x0 + $pw * $hh / 24;
        $svg .= '<line x1="' . $x . '" y1="' . $y0 . '" x2="' . $x . '" y2="' . ($y0 + $ph)
              . '" stroke="#eeeeee" stroke-width="1"/>';
        $svg .= '<text x="' . $x . '" y="' . ($h - 6)
              . '" font-size="9" fill="#999" text-anchor="middle">' . $hh . ':00</text>';
    }
    $poly = array();
    foreach ($punkte as $pt) {
        $anteil = ($pt[0] - $tag0) / 86400;
        if ($anteil < 0 || $anteil > 1) {
            continue;
        }
        $poly[] = round($x0 + $pw * $anteil, 1) . ','
                . round($y0 + $ph - $ph * max(0, min(100, $pt[1])) / 100, 1);
    }
    if (count($poly) >= 2) {
        $erst = explode(',', $poly[0]);
        $letzt = explode(',', $poly[count($poly) - 1]);
        $svg .= '<polygon points="' . $erst[0] . ',' . ($y0 + $ph) . ' ' . implode(' ', $poly) . ' '
              . $letzt[0] . ',' . ($y0 + $ph) . '" fill="#6dac20" opacity="0.15"/>';
        $svg .= '<polyline points="' . implode(' ', $poly) . '" fill="none" stroke="#6dac20" stroke-width="2"/>';
        $svg .= '<circle cx="' . $letzt[0] . '" cy="' . $letzt[1] . '" r="3" fill="#6dac20"/>';
    } else {
        $svg .= '<text x="' . ($x0 + $pw / 2) . '" y="' . ($y0 + $ph / 2)
              . '" font-size="11" fill="#aaa" text-anchor="middle">'
              . vw_e(vw_t($heute ? 'TEST.KEINE_MESSPUNKTE'
                                 : 'TEST.KEINE_MESSPUNKTE_TAG')) . '</text>';
    }
    return $svg . '</svg>';
}
