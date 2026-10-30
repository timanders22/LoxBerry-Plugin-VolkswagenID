<?php
/**
 * bin/befehle_zuordnung.php - Zuordnungstabelle der Befehlsbruecke, Linie VolkswagenID (Gruppe 41, 0.9.33).
 *
 * Die Befehlsbruecke (bin/befehlsbruecke.php und .sh, byte-gleich aus der Stammfassung Werkzeuge/gemeinsam/, 1.0.2)
 * abonniert <mqtt_topic>/befehl/# und gibt jeden gueltigen Befehl an tun() dieser Tabelle. tun() ruft DIESELBE
 * Funktion wie der HTTP-Endpunkt webfrontend/html/index.php - vw_befehl_annehmen() in vw_lib.php, Quelle 'mqtt'
 * (Pruefreihenfolge wie dort: Muster, Steuerung, zweiter Haken, S-PIN, Name, Zusatzwert, Fahrzeug, Dienst) -
 * und gibt array(ok, grund) fuer die Quittung zurueck (vw_befehl_quittung()). Ein Token gibt es auf diesem Weg
 * nicht; an seiner Stelle stehen die Haken "Befehle ueber MQTT annehmen" (befehle_mqtt_ein) und, fuer die
 * eingreifenden Befehle (Ver-/Entriegeln, Blinken, Hupen), "Eingreifende Befehle auch ueber MQTT annehmen"
 * (befehle_mqtt_steuern) ZUSAMMEN mit "Eingreifende Befehle zulassen" (eingreifend_ein) - alle ab Werk aus
 * (Entscheidung E4/E5 Gruppe 41; UDP 11884 ist im Heimnetz ohne Anmeldung erreichbar). Die S-PIN-Pflicht fuer
 * Ver- und Entriegeln gilt auf diesem Weg genauso (SPIN_FEHLT).
 *
 * Quittungsart "uebergeben" (E3): die Bruecke wartet nie - angenommen heisst, der Befehl liegt in der
 * Warteschlange des Dienstes. Der Dienst prueft danach ein zweites Mal (Steuerung, zweiter Haken, Wertgrenzen,
 * Entprellung, Stundenbremse fuer alle Wege gemeinsam) und schreibt das Ergebnis ins Protokoll.
 *
 * Sperrzeiten (Bauvorschrift G41 G2): Sollwerte und Zustaende 0 s - VW hat keine Gleichwert-Bremse in PHP; die
 * Entprellung des Dienstes (gleicher Befehl, gleicher Wert binnen 'entprellung' s, Vorgabe 20 s) bleibt die eine
 * Pruefung. Ereignisse (auffrischen, wecken, blinken, hupen) 60 s (Vorgabe der Bruecke).
 * Fahrzeug 1 unter <p>/befehl/<name>, Fahrzeug 2-9 unter <p>/befehl/<n>/<name> (Teil {nr}).
 * Ladegrenze 50-100 % (Grenzen des Connectors, die der Dienst prueft; Tabelle G2 nannte 10-100).
 * Die Datei setzt keine Konfiguration und schreibt nichts; wie der unangemeldete Endpunkt heilt die Bruecke
 * nichts (vw_config(false)).
 */
if (!function_exists('vw_befehl_annehmen')) {
    $vw_kand = array();
    $vw_kand[] = 'REPLACELBPHTMLDIR/vw_lib.php';
    $vw_kand[] = dirname(__DIR__) . '/webfrontend/html/vw_lib.php';
    if (basename(dirname(__DIR__)) === 'plugins') {
        $vw_kand[] = dirname(dirname(dirname(__DIR__))) . '/webfrontend/html/plugins/' . basename(__DIR__) . '/vw_lib.php';
    }
    foreach ($vw_kand as $vw_k) {
        if (strpos($vw_k, 'REPLACE') !== 0 && is_file($vw_k)) {
            require_once $vw_k;
            break;
        }
    }
}
$vw_grund_soll = 'Die Entprellung des Dienstes (gleicher Befehl, gleicher Wert binnen der eingestellten Sekunden) '
    . 'bleibt die eine Pruefung (Bauvorschrift Gruppe 41, G2).';
/* Ein Befehl der Bruecke -> (Aktion des Endpunkts, Zusatzwerte) fuer vw_befehl_annehmen(). */
$vw_tun = function ($wie) {
    return function ($w, $t) use ($wie) {
        $nr = isset($t['nr']) ? (string) (int) $t['nr'] : '1';
        list($aktion, $param) = $wie((string) $w);
        if ($aktion === 'aussentemp') {
            return vw_befehl_quittung(vw_aussentemp_merken($param['wert']));
        }
        return vw_befehl_quittung(vw_befehl_annehmen($aktion, $param, $nr, 'mqtt'));
    };
};
$vw_fest = function ($a) { return function ($w) use ($a) { return array($a, array()); }; };
$vw_mit = function ($a, $feld) { return function ($w) use ($a, $feld) { return array($a, array($feld => $w)); }; };
$vw_schalter = function ($an, $aus) { return function ($w) use ($an, $aus) { return array($w === '1' ? $an : $aus, array()); }; };
$vw_einst = function ($name) { return function ($w) use ($name) { return array('einstellung', array('name' => $name, 'wert' => $w)); }; };
/* Name => array(Art, eingreifend, sperre_s|null fuer die Vorgabe 60, Titel in Loxone, Kachel, Zusatz, Aufruf) */
$vw_def = array(
    'auffrischen'    => array('puls', false, null, 'VW Werte abfragen (MQTT)', 'BEFEHL.K_AUFFRISCHEN', array(), $vw_fest('abruf')),
    'laden'          => array('schalter', false, 0, 'VW Laden (MQTT)', 'BEFEHL.K_LADEN', array(), $vw_schalter('laden_start', 'laden_stop')),
    'klima_an'       => array('zahl', false, 0, 'VW Klimatisierung ein (MQTT)', 'BEFEHL.K_KLIMA_AN', array('min' => 10, 'max' => 30), $vw_mit('klima_start', 'temp')),
    'klima_aus'      => array('puls', false, 0, 'VW Klimatisierung aus (MQTT)', 'BEFEHL.K_KLIMA_AUS', array(), $vw_fest('klima_stop')),
    'zieltemperatur' => array('zahl', false, 0, 'VW Zieltemperatur (MQTT)', 'BEFEHL.K_ZIELTEMP', array('min' => 10, 'max' => 30), $vw_mit('zieltemperatur', 'temp')),
    'ladegrenze'     => array('ganz', false, 0, 'VW Ladegrenze (MQTT)', 'BEFEHL.K_LADEGRENZE', array('min' => 50, 'max' => 100), $vw_mit('ladegrenze', 'prozent')),
    'ladestrom'      => array('auswahl', false, 0, 'VW Ladestrom (MQTT)', 'BEFEHL.K_LADESTROM', array('werte' => array('5', '6', '10', '13', '16', '32')), $vw_mit('ladestrom', 'ampere')),
    'scheibe'        => array('schalter', false, 0, 'VW Scheibenheizung (MQTT)', 'BEFEHL.K_SCHEIBE', array(), $vw_schalter('scheibe_ein', 'scheibe_aus')),
    'wecken'         => array('puls', false, null, 'VW Fahrzeug wecken (MQTT)', 'BEFEHL.K_WECKEN', array(), $vw_fest('wecken')),
    'einstellung_klima_entriegeln' => array('schalter', false, 0, 'VW Klimatisierung beim Entriegeln (MQTT)', 'BEFEHL.K_E_KLIMA_ENTRIEGELN', array(), $vw_einst('klima_entriegeln')),
    'einstellung_stecker_auto'     => array('schalter', false, 0, 'VW Stecker nach dem Laden entriegeln (MQTT)', 'BEFEHL.K_E_STECKER_AUTO', array(), $vw_einst('stecker_auto')),
    'einstellung_klima_ohne_netz'  => array('schalter', false, 0, 'VW Klimatisieren ohne Netzanschluss (MQTT)', 'BEFEHL.K_E_KLIMA_OHNE_NETZ', array(), $vw_einst('klima_ohne_netz')),
    // E5 (Gruppe 41): eingreifend - nur mit befehle_mqtt_steuern UND eingreifend_ein; S-PIN-Pflicht bleibt.
    'verriegeln'     => array('puls', true, 0, 'VW Verriegeln (MQTT)', 'BEFEHL.K_VERRIEGELN', array(), $vw_fest('verriegeln')),
    'entriegeln'     => array('puls', true, 0, 'VW Entriegeln (MQTT)', 'BEFEHL.K_ENTRIEGELN', array(), $vw_fest('entriegeln')),
    'blinken'        => array('puls', true, null, 'VW Blinken (MQTT)', 'BEFEHL.K_BLINKEN', array(), $vw_fest('blinken')),
    'hupen'          => array('puls', true, null, 'VW Hupen (MQTT)', 'BEFEHL.K_HUPEN', array(), $vw_fest('hupen')),
);
$vw_befehle_mqtt = array();
foreach (array('' => '', '{nr}/' => ' Fahrzeug N') as $vw_vor => $vw_zus) {
    foreach ($vw_def as $vw_name => $vw_d) {
        $vw_e = array('art' => $vw_d[0], 'eingreifend' => $vw_d[1],
                      'titel' => $vw_zus === '' ? $vw_d[3] : preg_replace('/^VW /', 'VW Fahrzeug N ', $vw_d[3]),
                      'kachel' => $vw_d[4], 'quittung' => 'uebergeben', 'tun' => $vw_tun($vw_d[6]));
        if ($vw_d[2] !== null) {
            $vw_e['sperre_s'] = $vw_d[2];
            $vw_e['begruendung'] = $vw_grund_soll;
        }
        $vw_befehle_mqtt[$vw_vor . $vw_name] = $vw_e + $vw_d[5];
    }
}
/* G39 E-E4 / G41 G5: die Aussentemperatur der Hauswetterstation aus Loxone - nur einmal, gilt fuer alle
 * Fahrzeuge (Wettergrenze der Vorklimatisierung, 30 min gueltig). */
$vw_befehle_mqtt['aussentemp'] = array('art' => 'zahl', 'eingreifend' => false, 'min' => -40, 'max' => 60,
    'sperre_s' => 0, 'begruendung' => 'Ein Messwert: jeder neue Wert zaehlt, auch ein gleicher (frischt das Alter auf).',
    'titel' => 'VW Außentemperatur aus Loxone (MQTT)', 'kachel' => 'BEFEHL.K_AUSSENTEMP', 'quittung' => 'uebergeben',
    'tun' => $vw_tun(function ($w) { return array('aussentemp', array('wert' => $w)); }));
return array(
    'fassung' => 1,
    'linie'   => 'volkswagenid',
    'plugin'  => 'VW',
    'geladen' => function_exists('vw_befehl_annehmen') && function_exists('vw_befehl_quittung')
                 && function_exists('vw_config') && function_exists('vw_aussentemp_merken'),
    'zweiter_haken' => 'befehle_mqtt_steuern',
    'einstellungen' => function () {
        $c = vw_config(false);   // wie der unangemeldete Endpunkt: nichts heilen, nichts anlegen
        $p = trim((string) $c['mqtt_topic'], '/ ');
        return array('praefix'     => $p === '' ? 'volkswagen' : $p,
                     'annehmen'    => !empty($c['befehle_mqtt_ein']),
                     // E5: eingreifend nur mit dem zweiten Haken UND "Eingreifende Befehle zulassen"
                     'eingreifend' => !empty($c['befehle_mqtt_steuern']) && !empty($c['eingreifend_ein']),
                     'mqtt_ein'    => !empty($c['mqtt_ein']));
    },
    'protokoll' => function ($stufe, $text) {
        vw_log_zeile('Befehlsbruecke: ' . ($stufe === 'INFO' ? '' : $stufe . ' ') . $text);
    },
    'teile' => array('nr' => '[2-9]'),
    'befehle' => $vw_befehle_mqtt,
);
