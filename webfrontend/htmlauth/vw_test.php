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

    return $zeilen;
}

/**
 * Welche Reiternamen kennen Liste, Beschriftungstabelle und Bereiche?
 *
 * Gelesen wird der QUELLTEXT der Oberflaeche, nicht der Zustand zur Laufzeit.
 * Nur so faellt ein Bereich auf, den es in der Datei gibt und den zur Laufzeit
 * niemand oeffnet.
 *
 * Die Reiterleiste wird NICHT verglichen, und das ist keine Luecke: sie
 * entsteht in einer Schleife ueber dieselbe Liste und kann deshalb nicht
 * abweichen. Woertliche Eintraege in der Leiste - die es hier nicht gibt -
 * wuerden trotzdem auffallen; sie werden mitgelesen.
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

        default:
            return array(0, vw_t('TEST.M_UNBEKANNT'));
    }
}

/** Mini-SVG: Fuellstand ueber den heutigen Tag (0 bis 24 h, 0 bis 100 %). */
function vw_soc_svg($punkte)
{
    $w = 720; $h = 120; $x0 = 34; $y0 = 8; $pw = $w - $x0 - 8; $ph = $h - $y0 - 20;
    $tag0 = strtotime('today 00:00');
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
              . vw_e(vw_t('TEST.KEINE_MESSPUNKTE')) . '</text>';
    }
    return $svg . '</svg>';
}
