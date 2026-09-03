<?php
/**
 * Volkswagen ID - gemeinsame Bibliothek
 *
 * Liegt bewusst unter webfrontend/html/, weil der Miniserver-Endpunkt sie
 * ebenso braucht wie die Oberflaeche. Nur so gibt es EINE Datei statt zweier
 * Kopien, die auseinanderlaufen. Die Oberflaeche unter htmlauth/ laedt sie von
 * hier (drei Kandidatenpfade: installiert und im Archiv).
 *
 * Die Bibliothek spricht NIE mit der Volkswagen-Schnittstelle. Sie liest den
 * Zwischenspeicher, den bin/vw.py schreibt, und legt Schreibbefehle in
 * einer Warteschlange ab. Ein Plugin, das den Datenabruf in der Oberflaeche
 * oder im Endpunkt erledigt, ist falsch gebaut - auch wenn es funktioniert.
 *
 * Praefix 'vw_', weil LBWeb::lbheader() SDK-Globale setzt und gleichnamige
 * Plugin-Variablen ueberschreiben wuerde.
 * Kompatibel mit PHP 7.4 und PHP 8.x (LoxBerry 3.x/4.x).
 */

if (!function_exists('vw_e')) {
    function vw_e($s)
    {
        return htmlspecialchars((string) $s, ENT_QUOTES, 'UTF-8');
    }
}


/* Den LoxBerry-Wurzelordner ohne festen Systempfad bestimmen.
 *
 * Vom eigenen Ablageort aufwaerts, bis ein Verzeichnis gefunden ist, das
 * config/plugins UND webfrontend enthaelt. Das trifft die uebliche
 * Installation genauso wie eine an einem anderen Ort - und es trifft auch
 * den Fall, dass das Plugin noch als entpacktes Archiv daliegt (dann findet
 * es nichts und gibt einen Leerstring zurueck, was der Aufrufer ohnehin
 * abfangen muss).
 *
 * Der Name traegt kein Plugin-Kuerzel und ist deshalb abgesichert: zwei
 * Bibliotheken landen nie im selben Prozess, aber die Pruefung kostet nichts.
 */
if (!function_exists('lb_wurzel_ermitteln')) {
    function lb_wurzel_ermitteln()
    {
        $d = __DIR__;
        for ($i = 0; $i < 8; $i++) {
            if (is_dir($d . '/config/plugins') && is_dir($d . '/webfrontend')) {
                return $d;
            }
            $eltern = dirname($d);
            if ($eltern === $d) { break; }
            $d = $eltern;
        }
        return '';
    }
}

function vw_paths()
{
    static $p = null;
    if ($p !== null) {
        return $p;
    }
    $home = getenv('LBHOMEDIR');
    if (!$home || !is_dir($home)) {
        foreach (array(lb_wurzel_ermitteln(), '/home/loxberry/loxberry') as $k) {
            if (is_dir($k)) {
                $home = $k;
                break;
            }
        }
    }
    // Der Pluginordner ergibt sich aus dem Ablageort dieser Datei. Der
    // MD5-Schluessel aus der plugindatabase.json wird bewusst NICHT benutzt -
    // er wird aus Autorenname, E-Mail und Plugin-Name gebildet und aendert
    // sich bei jedem Fork.
    $dir = basename(dirname(__FILE__));
    /* Frueher wurde hier auf den festen Namen "volkswagenid" zurueckgefallen,
     * sobald config/plugins/<ordner> noch fehlte - etwa im Augenblick der
     * Installation. Haengt LoxBerry bei einer Zweitinstallation einen Zaehler
     * an (volkswagenid_01, weil der Name schon belegt war), zeigten deren
     * Pfade damit auf die ERSTE Installation: gemeinsame Konfiguration - und
     * darin stehen Zugangsdaten und Anmeldemarken -, gemeinsame
     * Warteschlange, gemeinsames Protokoll.
     *
     * LBPPLUGINDIR ist die Auskunft von LoxBerry selbst und bleibt deshalb.
     * Der feste Name greift nur noch dort, wo der ermittelte nachweislich kein
     * Plugin-Ordner sein kann: aus dem ausgepackten Archiv heraus heisst er
     * "html". */
    $lbp = getenv('LBPPLUGINDIR');
    if ($lbp) {
        $dir = $lbp;
    } elseif ($dir === '' || $dir === '.' || $dir === '/' || $dir === 'html') {
        $dir = 'volkswagenid';
    }
    if ($home) {
        $p = array(
            'home'      => $home,
            'plugin'    => $dir,
            'configdir' => $home . '/config/plugins/' . $dir,
            'config'    => $home . '/config/plugins/' . $dir . '/vw.json',
            'zugang'    => $home . '/config/plugins/' . $dir . '/zugang.json',
            'sicherung' => $home . '/config/plugins/' . $dir . '.backup.vw.json',
            'zugang_sicherung' => $home . '/config/plugins/' . $dir . '.backup.zugang.json',
            'datadir'   => $home . '/data/plugins/' . $dir,
            'bindir'    => $home . '/bin/plugins/' . $dir,
            'logdir'    => $home . '/log/plugins/' . $dir,
            'log'       => $home . '/log/plugins/' . $dir . '/vw.log',
            'ladungen'  => $home . '/data/plugins/' . $dir . '/ladungen.csv',
        );
    } else {
        // Nicht installiert (Entwicklung, Attrappe): neben dem Plugin arbeiten.
        $basis = dirname(dirname(__DIR__));
        $p = array(
            'home'      => '',
            'plugin'    => $dir,
            'configdir' => $basis . '/config',
            'config'    => $basis . '/config/vw.json',
            'zugang'    => $basis . '/config/zugang.json',
            'sicherung' => $basis . '/config/vw.backup.json',
            'zugang_sicherung' => $basis . '/config/zugang.backup.json',
            'datadir'   => $basis . '/data',
            'bindir'    => $basis . '/bin',
            'logdir'    => $basis . '/log',
            'log'       => $basis . '/log/vw.log',
            'ladungen'  => $basis . '/data/ladungen.csv',
        );
    }
    return $p;
}

/**
 * Voreinstellungen. Muessen zu VORGABEN in bin/vw.py passen.
 *
 * Die beiden Schluessel 'aktionstoken' und 'wartezeit' kennt nur die
 * Oberflaeche - der Dienst braucht sie nicht. Alle uebrigen stehen in beiden
 * Dateien mit demselben Wert; vw_pruefen.py zaehlt das nach.
 *
 * Diese Liste ist zugleich die Positivliste der Sicherungsdatei: was hier
 * nicht steht, wird beim Zurueckspielen als fremd beanstandet.
 */
function vw_vorgaben()
{
    return array(
        'intervall'         => 300,
        'takt_wartung'      => 12,
        'mqtt_ein'          => 0,
        'mqtt_topic'        => 'volkswagen',
        'mqtt_retain'       => 1,
        'steuerung_ein'     => 0,
        'eingreifend_ein'   => 0,
        'temp_min'          => 16,
        'temp_max'          => 29,
        'verlauf_tage'      => 8,
        'zugriff_erzwingen' => 0,
        'heim_breite'       => '',
        'heim_laenge'       => '',
        'heim_radius'       => 150,
        'abstand_abruf'     => 120,
        'befehle_stunde'    => 30,
        'entprellung'       => 20,
        'empf_thema'        => '',
        'empf_grenze'       => '',
        'empf_kleiner'      => 1,
        'abfahrt_ein'       => 0,
        'abfahrt_thema'     => '',
        'abfahrt_vorlauf'   => 20,
        'abfahrt_temp'      => 21,
        'aktionstoken'      => '',
        'wartezeit'         => 8,
        'wartezeit_endpunkt' => 3,
    );
}

/**
 * Die zulaessigen Werte je Einstellung - an EINER Stelle.
 *
 * Drei Verbraucher lesen daraus: das Formular beim Speichern, die
 * Sicherungsdatei beim Zurueckspielen und der Endpunkt. Eine zweite Wahrheit
 * ueber zulaessige Werte gibt es nicht; sonst laesst die eine Stelle durch,
 * was die andere abweist, und niemand merkt es.
 *
 * Form: 'schluessel' => array(art, ...)
 *   ganz:   array('ganz', min, max)
 *   schalt: array('schalt')                 0 oder 1
 *   text:   array('text', muster, maxlaenge)
 *   zahl:   array('zahl', min, max)         Kommazahl, '' erlaubt
 */
function vw_regeln()
{
    return array(
        'intervall'          => array('ganz', 180, 3600),
        'takt_wartung'       => array('ganz', 1, 240),
        'mqtt_ein'           => array('schalt'),
        'mqtt_topic'         => array('text', '#^[A-Za-z0-9_\-]+(/[A-Za-z0-9_\-]+)*$#', 64),
        'mqtt_retain'        => array('schalt'),
        'steuerung_ein'      => array('schalt'),
        'eingreifend_ein'    => array('schalt'),
        'temp_min'           => array('ganz', 10, 30),
        'temp_max'           => array('ganz', 10, 30),
        'verlauf_tage'       => array('ganz', 1, 90),
        'zugriff_erzwingen'  => array('schalt'),
        'heim_breite'        => array('zahl', -90, 90),
        'heim_laenge'        => array('zahl', -180, 180),
        'heim_radius'        => array('ganz', 10, 5000),
        'abstand_abruf'      => array('ganz', 0, 3600),
        'befehle_stunde'     => array('ganz', 1, 240),
        'entprellung'        => array('ganz', 0, 600),
        'empf_thema'         => array('text', '#^[A-Za-z0-9_\-/]*$#', 128),
        'empf_grenze'        => array('zahl', -100000, 100000),
        'empf_kleiner'       => array('schalt'),
        'abfahrt_ein'        => array('schalt'),
        'abfahrt_thema'      => array('text', '#^[A-Za-z0-9_\-/]*$#', 128),
        'abfahrt_vorlauf'    => array('ganz', 5, 180),
        'abfahrt_temp'       => array('ganz', 10, 30),
        /* Das Aktionstoken: bewusst WEIT gefasst.
         *
         * vw_token_erzeugen() bildet nur Kleinbuchstaben und Ziffern - aber
         * ein Token kann von Hand gesetzt, aus einer aelteren Fassung
         * uebernommen oder von einem Pruefstand vorgegeben sein. Beim ersten
         * Messen dieser Fassung stand hier '#^[a-z0-9]{0,64}$#', und der
         * Pruefstand mit dem Token 'PRUEFTOKEN1234' fiel durch: der Wert wurde
         * abgewiesen, die Vorgabe (leer) trat an seine Stelle, und vw_token()
         * erzeugte ein neues. Auf einer echten Anlage haette das JEDE im
         * Miniserver eingetragene Adresse ungueltig gemacht - stumm, denn ein
         * Virtueller Ausgang wertet die 403-Antwort nicht aus.
         *
         * Zugelassen ist deshalb alles, was ohne Kodierung in eine Adresse
         * passt. Abgewiesen wird, was dort Schaden anrichtet. */
        /* {0,64} - die Laenge 0 bleibt ZULAESSIG, und das ist Absicht.
         *
         * Beim Bau von 0.9.12 stand hier zuerst {16,64}, dann {1,64}. Beides
         * war falsch, und der Pruefstand hat es gefunden: sicherung_wirkung.py
         * baut seine gueltige Probe aus vw_vorgaben(), und dort ist das Token
         * leer - die Probe wurde abgelehnt, "gueltig:ROT" an einer Funktion,
         * die richtig arbeitet. Ein leeres Token in einer SICHERUNGSDATEI
         * heisst "kein Token gesichert" und ist kein unzulaessiger Wert.
         *
         * Ein zu enges Muster waere ausserdem der Vorfall vom 27.08.2026 noch
         * einmal: es verwirft ein gueltiges, von Hand gesetztes Token, und
         * der Schaden ist derselbe wie bei einem verlorenen.
         *
         * Die Frage "fehlt hier ein Token, obwohl schon eine Konfiguration
         * besteht?" gehoert nicht in die Wertepruefung, sondern in vw_token()
         * - dort wird sie seit 0.9.12 gestellt und protokolliert. Und wie
         * stark das Token ist, meldet der Reiter Test. */
        'aktionstoken'       => array('text', '#^[A-Za-z0-9_.\-]{0,64}$#', 64),
        'wartezeit'          => array('ganz', 0, 30),
        'wartezeit_endpunkt' => array('ganz', 0, 15),
    );
}

/**
 * Taugt der Wert ueberhaupt fuer eine Zeile dieser Konfiguration?
 *
 * Die erste von zwei Wachen. Sie fragt nicht, ob der Wert zur Einstellung
 * passt, sondern ob er ueberhaupt ein Wert ist: kein Feld, kein Objekt,
 * kein Steuerzeichen, nicht endlos lang. Ein Feld im Tokenfeld hat am
 * Endpunkt eine PHP-Warnung erzeugt und "Array" als Token verglichen -
 * gemessen am 27.08.2026.
 */
function vw_wert_taugt($v)
{
    if (is_array($v) || is_object($v) || is_null($v)) {
        return false;
    }
    if (is_bool($v)) {
        return false;
    }
    $s = (string) $v;
    if (strlen($s) > 4096) {
        return false;
    }
    return preg_match('/[\x00-\x08\x0A-\x1F\x7F]/', $s) !== 1;
}

/**
 * Ist der Wert fuer DIESE Einstellung zulaessig?
 *
 * Die zweite Wache, gegen vw_regeln(). Rueckgabe: array(ok, bereinigter Wert).
 * Bereinigt heisst hier ausschliesslich: die Zahl als int oder float statt als
 * Zeichenkette. Es wird nichts gekappt und nichts zurechtgebogen - ein still
 * veraenderter Sollwert fuehrt zu einem Fahrzeug, das etwas anderes tut als
 * angezeigt.
 */
function vw_wert_pruefen($schluessel, $wert)
{
    $regeln = vw_regeln();
    if (!isset($regeln[$schluessel])) {
        return array(false, null);
    }
    if (!vw_wert_taugt($wert)) {
        return array(false, null);
    }
    $r = $regeln[$schluessel];
    $s = trim((string) $wert);
    switch ($r[0]) {
        case 'ganz':
            if (!preg_match('/^-?[0-9]+$/', $s)) {
                return array(false, null);
            }
            $n = (int) $s;
            return ($n >= $r[1] && $n <= $r[2]) ? array(true, $n) : array(false, null);
        case 'schalt':
            // Genau 0 oder 1. Die Zeichenkette "0" ist in PHP leer, in Python
            // aber wahr - ein solcher Wert oeffnete das Schreibtor, waehrend
            // die Oberflaeche "gesperrt" anzeigt. Gemessen am 27.08.2026.
            return ($s === '0' || $s === '1') ? array(true, (int) $s) : array(false, null);
        case 'text':
            if (strlen($s) > $r[2]) {
                return array(false, null);
            }
            return preg_match($r[1], $s) ? array(true, $s) : array(false, null);
        case 'zahl':
            if ($s === '') {
                return array(true, '');
            }
            if (!preg_match('/^-?[0-9]+([.,][0-9]+)?$/', $s)) {
                return array(false, null);
            }
            $f = (float) str_replace(',', '.', $s);
            return ($f >= $r[1] && $f <= $r[2]) ? array(true, $f) : array(false, null);
    }
    return array(false, null);
}

function vw_json_lesen($pfad)
{
    if (!is_file($pfad)) {
        return array();
    }
    $d = json_decode((string) @file_get_contents($pfad), true);
    return is_array($d) ? $d : array();
}

/**
 * Die Konfiguration - vollstaendig, geprueft, und mit einer Auskunft darueber,
 * in welchem Zustand sie vorgefunden wurde.
 *
 * $erzeugen = false schaltet JEDES Schreiben ab. Der unangemeldete Endpunkt
 * ruft so auf: bis 0.9.9 hat ein einziger Aufruf OHNE Token - korrekt mit 403
 * beantwortet - die Konfigurationsdatei aus der Zweitschrift zurueckgeschrieben
 * (gemessen am 27.08.2026). Wer sich nicht ausweisen kann, legt nichts an,
 * auch nichts Harmloses.
 *
 * Vier Zustaende, und jeder hat seinen Satz:
 *   ok               die Datei war da und lesbar
 *   leer             sie fehlte oder war leer
 *   kaputt           sie enthielt kein gueltiges JSON
 *   aus_zweitschrift der Stand kommt aus der Kopie neben dem Konfigordner
 *
 * Bis 0.9.9 pruefte die Selbstheilung auf '' und '{}'. Eine beim Schreiben
 * abgeschnittene Datei - Stromausfall - ist keins von beidem, ergibt
 * json_decode() === null und damit die Werkseinstellung mit LEEREM Token.
 * vw_token() erzeugte daraufhin ein neues und schrieb es ueber die
 * Zweitschrift: gemessen gingen dabei Takt, Thema, Steuerungshaken und alle
 * Loxone-Adressen verloren, und die Rettung gleich mit.
 *
 * Deshalb: die Zweitschrift wird GELESEN, nicht kopiert, die beschaedigte
 * Datei bleibt als .kaputt liegen, und erst nach einem gelungenen Lesen wird
 * zurueckgeschrieben.
 */
function vw_config($erzeugen = true)
{
    $lage = vw_config_lesen($erzeugen);
    return $lage['cfg'];
}

/**
 * Wie vw_config(), gibt aber den ganzen Befund zurueck.
 *
 * array('cfg' => ..., 'lage' => 'ok|leer|kaputt|aus_zweitschrift',
 *       'abgewiesen' => array(schluessel => rohwert), 'fremd' => array(schluessel))
 *
 * 'abgewiesen' nennt die Werte, die gegen vw_regeln() durchgefallen sind und
 * durch die Vorgabe ersetzt wurden. Sie stehen dort, weil eine Datei von Hand
 * geschrieben, aus einer Sicherung zurueckgespielt oder aus einer aelteren
 * Fassung uebernommen sein kann - geprueft wird an beiden Enden.
 */
function vw_config_lesen($erzeugen = true)
{
    /* Der Zwischenspeicher liegt in einem Global, nicht in einer statischen
     * Variablen: er muss nach jedem Schreiben im selben Seitenaufbau
     * verworfen werden koennen, und eine Statik laesst sich von aussen nicht
     * zuruecksetzen. */
    if (!isset($GLOBALS['vw_cfg_speicher']) || !is_array($GLOBALS['vw_cfg_speicher'])) {
        $GLOBALS['vw_cfg_speicher'] = array();
    }
    $schluessel = $erzeugen ? 'j' : 'n';
    if (isset($GLOBALS['vw_cfg_speicher'][$schluessel])) {
        return $GLOBALS['vw_cfg_speicher'][$schluessel];
    }
    $p = vw_paths();
    $roh = is_file($p['config']) ? trim((string) @file_get_contents($p['config'])) : '';
    $lage = 'ok';
    $cfg = null;

    if ($roh === '' || $roh === '{}') {
        $lage = 'leer';
    } else {
        $d = json_decode($roh, true);
        if (is_array($d)) {
            $cfg = $d;
        } else {
            $lage = 'kaputt';
        }
    }

    /* Die urspruengliche Lage MERKEN, bevor die Zweitschrift sie ueberschreibt.
     *
     * Sonst geht der Beleg fuer den Vorfall verloren: greift die Zweitschrift,
     * steht die Lage auf 'aus_zweitschrift', und der Zweig, der die
     * beschaedigte Datei als .kaputt sichert, laeuft nicht mehr an. Genau das
     * ist beim ersten Messen dieser Fassung passiert - die Wiederherstellung
     * war richtig, nur der Beleg fehlte. */
    $war_kaputt = ($lage === 'kaputt');

    if ($cfg === null) {
        // Die Zweitschrift LESEN. Ein copy() wuerde eine kaputte Datei durch
        // eine heile ersetzen und dabei den einzigen Beleg vernichten.
        $sroh = is_file($p['sicherung']) ? trim((string) @file_get_contents($p['sicherung'])) : '';
        $ds = $sroh !== '' ? json_decode($sroh, true) : null;
        if (is_array($ds) && $ds) {
            $cfg = $ds;
            $lage = 'aus_zweitschrift';
        } else {
            $cfg = array();
        }
    }

    // ---- Jeden Wert gegen vw_regeln() halten ----
    $vorgaben = vw_vorgaben();
    $fertig = $vorgaben;
    $abgewiesen = array();
    $fremd = array();
    foreach ($cfg as $k => $v) {
        if (!array_key_exists($k, $vorgaben)) {
            $fremd[] = $k;
            continue;
        }
        list($ok, $rein) = vw_wert_pruefen($k, $v);
        if ($ok) {
            $fertig[$k] = $rein;
        } else {
            $abgewiesen[$k] = is_scalar($v) ? (string) $v : gettype($v);
        }
    }
    // temp_min > temp_max ist keine Ablehnung, sondern ein Tausch: beide Werte
    // sind fuer sich zulaessig, nur ihre Reihenfolge ist es nicht.
    if ($fertig['temp_min'] > $fertig['temp_max']) {
        $t = $fertig['temp_min'];
        $fertig['temp_min'] = $fertig['temp_max'];
        $fertig['temp_max'] = $t;
    }

    // ---- Zurueckschreiben, aber nur wo Schreiben erlaubt ist ----
    if ($erzeugen && $lage !== 'ok') {
        /* Die beschaedigte Datei bleibt liegen - EINMAL. Ein zweites
         * Ueberschreiben wuerde den ersten Beleg durch den zweiten ersetzen,
         * und der erste ist der interessante. */
        if ($war_kaputt && $roh !== '' && !is_file($p['config'] . '.kaputt')) {
            @copy($p['config'], $p['config'] . '.kaputt');
            @chmod($p['config'] . '.kaputt', 0600);
        }
        if ($lage === 'aus_zweitschrift' || $lage === 'leer' || $war_kaputt) {
            vw_config_schreiben($fertig, false);   // Zweitschrift NICHT anfassen
        }
    }

    $GLOBALS['vw_cfg_speicher'][$schluessel] =
        array('cfg' => $fertig, 'lage' => $lage,
              'abgewiesen' => $abgewiesen, 'fremd' => $fremd);
    return $GLOBALS['vw_cfg_speicher'][$schluessel];
}

/**
 * Schreibt die Konfigurationsdatei. $zweitschrift = false laesst die Kopie
 * neben dem Konfigordner unberuehrt.
 *
 * Ueber eine Nebendatei mit rename(): dann gibt es nur zwei Zustaende, alte
 * Datei oder neue. Eine halb geschriebene kann nicht mehr entstehen - genau
 * die hat den Befund von oben verursacht.
 *
 * Rechte 0600, und zwar auf der TEMPORAEREN Datei: in vw.json steht das
 * Aktionstoken des unangemeldeten Endpunkts.
 */
function vw_config_schreiben($cfg, $zweitschrift = true)
{
    $p = vw_paths();
    if (!is_dir($p['configdir'])) {
        @mkdir($p['configdir'], 0775, true);
    }
    $json = json_encode($cfg, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    // json_encode liefert bei ungueltigem UTF-8 false, und file_put_contents
    // schriebe dann eine Datei mit NULL Bytes - und meldete das als Erfolg.
    if ($json === false) {
        return false;
    }
    $tmp = $p['config'] . '.tmp.' . getmypid();
    if (@file_put_contents($tmp, $json) !== strlen($json)) {
        @unlink($tmp);
        return false;
    }
    @chmod($tmp, 0600);
    if (!@rename($tmp, $p['config'])) {
        @unlink($tmp);
        return false;
    }
    if ($zweitschrift) {
        $stmp = $p['sicherung'] . '.tmp.' . getmypid();
        if (@file_put_contents($stmp, $json) === strlen($json)) {
            @chmod($stmp, 0600);
            if (!@rename($stmp, $p['sicherung'])) {
                @unlink($stmp);
            }
        } else {
            @unlink($stmp);
        }
    }
    return true;
}

/** Beibehaltener Name. Schreibt Konfiguration UND Zweitschrift. */
function vw_config_speichern($cfg)
{
    $ok = vw_config_schreiben($cfg, true);
    if ($ok) {
        vw_config_zwischenspeicher_leeren();
    }
    return $ok;
}

/**
 * Den Zwischenspeicher von vw_config_lesen() verwerfen.
 *
 * Noetig nach jedem Schreiben im selben Seitenaufbau. Bis 0.9.9 stand der
 * Handler fuer das Zurueckspielen NACH dem Laden der Anzeigewerte: die
 * Konfigurationsdatei trug danach die neuen Werte, die Seite zeigte aber
 * neunzehnmal das alte Aktionstoken und jedes Feld auf altem Stand
 * (gemessen am 27.08.2026). Wer daraufhin auf Speichern drueckte, schrieb
 * den alten Stand zurueck.
 */
function vw_config_zwischenspeicher_leeren()
{
    $GLOBALS['vw_cfg_speicher'] = array();
}

/**
 * Zugangsdaten.
 *
 * Eigene Datei mit Rechten 0600, nicht in der Konfiguration, die die
 * Oberflaeche anzeigt. Passwort und S-PIN werden nie zurueckgegeben - nur
 * ihre Laenge.
 */
function vw_zugang()
{
    $z = vw_json_lesen(vw_paths()['zugang']);
    return array(
        'email'       => isset($z['email']) ? (string) $z['email'] : '',
        'laenge'      => isset($z['passwort']) ? strlen((string) $z['passwort']) : 0,
        'spin_laenge' => isset($z['spin']) ? strlen((string) $z['spin']) : 0,
    );
}

/**
 * Speichert die Zugangsdaten.
 *
 * Ein leer zurueckgegebenes Passwortfeld loescht nichts: sonst stuende
 * irgendwann ein leeres Passwort in der Datei, ohne dass es jemand merkt.
 * Genau dieser Fehler hat im ACTi-Plugin 21 vergebliche Anmeldeversuche
 * verursacht.
 */
function vw_zugang_speichern($email, $passwort, $spin)
{
    $p = vw_paths();
    if (!is_dir($p['configdir'])) {
        @mkdir($p['configdir'], 0775, true);
    }
    $alt = vw_json_lesen($p['zugang']);
    $neu = array(
        'email'    => $email !== null ? $email : (isset($alt['email']) ? $alt['email'] : ''),
        'passwort' => ($passwort !== null && $passwort !== '')
                      ? $passwort
                      : (isset($alt['passwort']) ? $alt['passwort'] : ''),
        'spin'     => ($spin !== null && $spin !== '')
                      ? $spin
                      : (isset($alt['spin']) ? $alt['spin'] : ''),
    );
    $json = json_encode($neu, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    // json_encode liefert bei ungueltigem UTF-8 false - dann darf nichts
    // geschrieben werden, sonst stuenden hier LEERE Zugangsdaten.
    if ($json === false) {
        return false;
    }
    /* Erst daneben schreiben, dann umbenennen. Ein einfaches
     * file_put_contents kuerzt die Datei und fuellt sie neu; der Dienst liest
     * dieselbe Datei beim Start und nach jeder Aenderung. Die Rechte werden
     * auf der TEMPORAEREN Datei gesetzt, nicht danach - sonst laege die Datei
     * einen Augenblick lang mit 0644 da, und in ihr steht ein Passwort. */
    $tmp = $p['zugang'] . '.tmp.' . getmypid();
    if (@file_put_contents($tmp, $json) === false) {
        return false;
    }
    @chmod($tmp, 0600);
    if (!@rename($tmp, $p['zugang'])) {
        @unlink($tmp);
        return false;
    }
    return true;
}

/**
 * Loescht E-Mail, Passwort und S-PIN restlos.
 *
 * Warum das ein eigenes Haekchen braucht: vw_zugang_speichern() behaelt ein
 * leeres Passwortfeld absichtlich bei - sonst stuende irgendwann ein leeres
 * Passwort in der Datei, ohne dass es jemand merkt. Genau diese Vorsicht
 * macht den umgekehrten Weg unmoeglich; wer sich vertippt hat oder das Konto
 * aus der Hand gibt, kam bis 0.9.0 ueber die Oberflaeche nicht mehr heran.
 *
 * MIT WEG MUSS DIE SICHERUNG. preupgrade.sh legt eine Kopie NEBEN dem
 * Konfigordner ab, und postinstall.sh spielt sie zurueck, wenn die richtige
 * Datei fehlt oder leer ist. Wuerde hier nur zugang.json geloescht, stuende
 * das Passwort weiterhin auf der Karte und waere bei der naechsten
 * Neuinstallation wieder da. Ein Loeschen, das nicht loescht, ist schlimmer
 * als keines.
 *
 * Die Anmeldemarken der Bibliothek gehen ebenfalls: in token.json steht ein
 * gueltiger Zugang zum Konto, auch ohne Passwort.
 */
function vw_zugang_loeschen()
{
    $p = vw_paths();
    $ok = true;
    $dateien = array($p['zugang'], $p['zugang_sicherung'],
                     $p['datadir'] . '/token.json');
    foreach ($dateien as $f) {
        if (!is_file($f)) {
            continue;
        }
        // Ueberschreiben, dann entfernen: nur unlink liesse den Inhalt auf
        // der Karte stehen, bis der Platz neu vergeben wird.
        // filesize() liest aus dem stat-Zwischenspeicher. In der
        // Weboberflaeche ist er je Aufruf frisch, der Fehler waere hier also
        // latent - die Klasse wird trotzdem geschlossen, sie kostet nichts.
        clearstatcache(true, $f);
        $laenge = (int) @filesize($f);
        if ($laenge > 0) {
            @file_put_contents($f, str_repeat('0', $laenge));
        }
        $ok = @unlink($f) && $ok;
    }
    return $ok;
}

/**
 * Die letzten $anzahl Zeilen einer Datei, neueste zuerst.
 *
 * Bis 0.9.0 las die Oberflaeche das ganze Protokoll mit file() ein und warf
 * fast alles wieder weg. Der Hinweis auf den Speicher war berechtigt - der
 * vorgeschlagene Weg ueber exec("tail") ist aber der langsamste der drei.
 * An einer Datei an der Rotationsgrenze gemessen, PHP 7.4 und 8.1:
 *
 *   file() + array_reverse   rund 0,3 ms   Spitze rund 1,4 MB
 *   exec("tail -n 400")      rund 1,9 ms   Spitze rund  75 kB
 *   rueckwaerts mit fseek    rund 0,05 ms  Spitze rund 125 kB
 *
 * Ein Prozessstart kostet mehr, als das Einlesen je gespart hat.
 */
function vw_log_ende($datei, $anzahl = 400, $block = 8192)
{
    $fp = @fopen($datei, 'rb');
    if ($fp === false) {
        return array();
    }
    fseek($fp, 0, SEEK_END);
    $pos = ftell($fp);
    $puffer = '';
    $zeilen = array();
    while ($pos > 0 && count($zeilen) <= $anzahl) {
        $lese = (int) min($block, $pos);
        $pos -= $lese;
        fseek($fp, $pos, SEEK_SET);
        $puffer = fread($fp, $lese) . $puffer;
        $zeilen = explode("\n", $puffer);
    }
    fclose($fp);
    $zeilen = array_values(array_filter(array_map('rtrim', $zeilen), 'strlen'));
    return array_slice(array_reverse($zeilen), 0, $anzahl);
}

/** Zufallstoken fuer den unangemeldeten Endpunkt. */
function vw_token_erzeugen($laenge = 24)
{
    $zeichen = 'abcdefghijkmnpqrstuvwxyz23456789';
    $t = '';
    for ($i = 0; $i < $laenge; $i++) {
        $t .= $zeichen[random_int(0, strlen($zeichen) - 1)];
    }
    return $t;
}

/**
 * Sorgt dafuer, dass ein Token vorhanden ist, und gibt es zurueck.
 *
 * Nur die ANGEMELDETE Oberflaeche ruft das auf. Der Endpunkt liest den Wert
 * unmittelbar und meldet KEIN_TOKEN_GESETZT, wenn keiner dasteht - wer sich
 * nicht ausweisen kann, loest kein Erzeugen aus.
 */
function vw_token()
{
    $lage = vw_config_lesen(true);
    $cfg = $lage['cfg'];
    if (trim((string) $cfg['aktionstoken']) === '') {
        /* Ein neues Token macht JEDE im Miniserver eingetragene Adresse
         * ungueltig. Wenn hier eines entsteht, weil das alte gegen vw_regeln()
         * durchgefallen ist, muss das im Protokoll stehen - sonst sucht der
         * Anwender einen Fehler in Loxone, den es dort nicht gibt. */
        /* Zwei Faelle, und nur der zweite ist harmlos:
         *
         *   - Es GAB ein Token, und es ist weg. Dann muss das ins Protokoll,
         *     sonst sucht der Betreiber den Fehler in Loxone, wo keiner ist.
         *   - Es gab noch nie eines (Neuinstallation). Dann ist das Entstehen
         *     der Normalfall und keine Meldung wert.
         *
         * Unterschieden wird an der Zweitschrift und an der Lage: eine
         * bestehende Anlage hat eine Konfiguration, eine frische nicht.
         * Bis 0.9.11 haing die Meldung allein an $lage['abgewiesen'] - ein
         * LEERES Token kam dort nie an, weil die Regel die Laenge 0 zuliess
         * (behoben, siehe vw_regeln). Damit blieb genau der Fall stumm, fuer
         * den diese Zeilen geschrieben wurden. */
        if (isset($lage['abgewiesen']['aktionstoken'])) {
            vw_log_zeile('Das hinterlegte Aktionstoken war unzulaessig und wurde durch '
                       . 'ein neues ersetzt. ALLE im Miniserver eingetragenen Adressen '
                       . 'muessen nachgezogen werden - Reiter Einbindung in Loxone.');
        } elseif ($lage['lage'] !== 'leer') {
            vw_log_zeile('Es war kein Aktionstoken hinterlegt, obwohl bereits eine '
                       . 'Konfiguration bestand - es wurde ein neues erzeugt. ALLE im '
                       . 'Miniserver eingetragenen Adressen muessen nachgezogen werden '
                       . '- Reiter Einbindung in Loxone. Haeufigste Ursache: eine '
                       . 'zurueckgespielte Sicherung ohne Token.');
        }
        $cfg['aktionstoken'] = vw_token_erzeugen();
        vw_config_speichern($cfg);
        $cfg = vw_config();
    }
    return (string) $cfg['aktionstoken'];
}

/**
 * Eine Zeile in die Logdatei des Plugins.
 *
 * Die Oberflaeche schreibt sonst nichts ins Protokoll - hier aber muss sie es:
 * ein neu erzeugtes Aktionstoken ist ein Vorgang, den man in einer Woche noch
 * nachlesen koennen muss.
 */
function vw_log_zeile($text)
{
    $p = vw_paths();
    if ($p['logdir'] === '') {
        return;
    }
    if (!is_dir($p['logdir'])) {
        @mkdir($p['logdir'], 0775, true);
    }
    @file_put_contents($p['log'], '[' . date('Y-m-d H:i:s') . '] WARNING '
        . str_replace(array("\r", "\n"), ' ', (string) $text) . "\n", FILE_APPEND);
}

/* ==================================================================
 * Wachposten - das Merkmal gegen fremde Absender
 *
 * htmlauth schuetzt gegen den unangemeldeten Aufruf - nicht dagegen, dass der
 * Browser eines angemeldeten Bedieners ein Formular abschickt, das auf einer
 * fremden Seite steht. Bis 0.9.9 fehlte das Merkmal ganz, obwohl ein Kommentar
 * in der Oberflaeche es beschrieb. Am Pruefstand mit php -S gemessen
 * (27.08.2026), drei Wirkungen eines einzigen fremden POST:
 *
 *   token_neu=1                  neues Aktionstoken - danach beantwortet der
 *                                Endpunkt jeden Virtuellen Ausgang mit 403,
 *                                und ein Virtueller Ausgang wertet die Antwort
 *                                nicht aus: der Ausfall bleibt still
 *   test=klima_start&temp=28     der Befehl lag in der Warteschlange, der
 *                                laufende Dienst arbeitet sie im Sekundentakt ab
 *   speichern=1&zugang_loeschen=1  Zugangsdaten UND Zweitschrift weg
 *
 * Das Merkmal wird aus dem Aktionstoken ABGELEITET, nicht gespeichert - sonst
 * hat die Konfiguration einen Schluessel mehr, den ein Speichern-Handler
 * vergessen kann.
 *
 * Fail closed: ohne hinterlegtes Token gibt es nichts zu vergleichen, und
 * hash_equals('', '') waere wahr.
 * ================================================================== */

function vw_formtoken($cfg = null)
{
    if ($cfg === null) {
        $cfg = vw_config();
    }
    $t = trim((string) (isset($cfg['aktionstoken']) ? $cfg['aktionstoken'] : ''));
    if ($t === '') {
        return '';
    }
    return hash_hmac('sha256', 'formular-v1', $t);
}

/** Das versteckte Feld fuer jedes Formular. */
function vw_formfeld($cfg = null)
{
    return '<input data-role="none" type="hidden" name="formtoken" value="'
         . vw_e(vw_formtoken($cfg)) . '">';
}

/**
 * Traegt dieser POST das gueltige Merkmal?
 *
 * Gelesen wird aus $_POST, nie aus $_REQUEST: sonst genuegte ein Anhaengsel
 * an der Adresse.
 */
function vw_formtoken_ok($cfg = null)
{
    $soll = vw_formtoken($cfg);
    if ($soll === '') {
        return false;
    }
    $ist = isset($_POST['formtoken']) && is_string($_POST['formtoken'])
         ? (string) $_POST['formtoken'] : '';
    return hash_equals($soll, $ist);
}

/* ---------------- Zwischenspeicher lesen ---------------- */

function vw_loxone()
{
    return vw_json_lesen(vw_paths()['datadir'] . '/loxone.json');
}

function vw_zustand()
{
    return vw_json_lesen(vw_paths()['datadir'] . '/zustand.json');
}

/** Fahrzeuge aus dem Abbild, 1-basiert. */
function vw_fahrzeuge()
{
    $l = vw_loxone();
    return isset($l['fahrzeuge']) && is_array($l['fahrzeuge']) ? $l['fahrzeuge'] : array();
}

/** Alter des Abbilds in Sekunden, oder -1 wenn es keines gibt. */
function vw_alter()
{
    $l = vw_loxone();
    return isset($l['ts']) ? max(0, time() - (int) $l['ts']) : -1;
}

/* ---------------- Dienst ---------------- */

function vw_dienst_pid()
{
    $f = vw_paths()['datadir'] . '/dienst.pid';
    if (!is_file($f)) {
        return 0;
    }
    $pid = (int) trim((string) @file_get_contents($f));
    if ($pid <= 0 || !is_dir('/proc/' . $pid)) {
        return 0;
    }
    /* Nummernrecycling ausschliessen: der Prozess muss unser Skript sein.
     *
     * Bis 0.9.0 stand hier strpos($cmd, 'vw.py'). Der Rahmen war schon
     * richtig - geprueft wird nur die Nummer aus der eigenen PID-Datei, es
     * wird nichts gesucht -, aber die Pruefung selbst zu weich: /proc/<pid>/
     * cmdline enthaelt ALLE Argumente, durch Nullbytes getrennt. Hat die
     * wiederverwendete Nummer einen Editor mit geoeffneter vw.py erwischt,
     * galt der als laufender Dienst.
     *
     * Verglichen wird jetzt argumentweise gegen den vollen Pfad. Das trifft
     * auch den Fall zweier Exemplare des Plugins: LoxBerry haengt bei
     * Namenskonflikt 01, 02 ... an den Ordnernamen an. */
    $cmd = (string) @file_get_contents('/proc/' . $pid . '/cmdline');
    $argv = explode("\0", $cmd);
    $skript = vw_paths()['bindir'] . '/vw.py';
    /* Zwei Bedingungen, nicht eine:
     *   argv[1] ist genau unser Skript UND
     *   argv[0] ist ein Python.
     * Die zweite braucht es, weil "nano /pfad/vw.py" ebenfalls den vollen
     * Pfad als zweites Argument fuehrt - nachgestellt und bestaetigt. Der
     * Dienst wird immer als "<venv>/bin/python3 <pfad>/vw.py" gestartet. */
    if (isset($argv[0], $argv[1])
        && $argv[1] === $skript
        && preg_match('#(^|/)python[0-9.]*$#', $argv[0])) {
        return $pid;
    }
    return 0;
}

function vw_dienst_soll()
{
    return is_file(vw_paths()['datadir'] . '/soll_laufen') ? 1 : 0;
}

/** $befehl ist 'start', 'stop' oder 'restart'. Rueckgabe: array(ok, Ausgabe) */
function vw_dienst($befehl)
{
    if (!in_array($befehl, array('start', 'stop', 'restart'), true)) {
        return array(0, 'Unbekannter Befehl.');
    }
    $skript = vw_paths()['bindir'] . '/dienst.sh';
    if (!is_file($skript)) {
        return array(0, 'dienst.sh nicht gefunden: ' . $skript);
    }
    $ausgabe = array();
    $code = 0;
    @exec(escapeshellcmd($skript) . ' ' . escapeshellarg($befehl) . ' 2>&1', $ausgabe, $code);
    return array($code === 0 ? 1 : 0, implode("\n", $ausgabe));
}

/**
 * Fassungen der beiden Python-Pakete in der virtuellen Umgebung.
 *
 * Es sind zwei: der Kern (carconnectivity) und der Volkswagen-Connector. Beide
 * werden getrennt veroeffentlicht und koennen auseinanderlaufen - deshalb
 * stehen beide in der Oberflaeche.
 *
 * Rueckgabe: array('kern' => '0.11.10', 'connector' => '0.10.6'); nicht
 * ermittelbare Werte bleiben ''.
 */
function vw_bibliothek_fassungen()
{
    $py = vw_paths()['bindir'] . '/venv/bin/python3';
    $leer = array('kern' => '', 'connector' => '');
    if (!is_file($py)) {
        return $leer;
    }
    $ausgabe = array();
    @exec(escapeshellcmd($py) . ' -c ' . escapeshellarg(
        'import importlib.metadata as m' . "\n"
        . 'for p in ("carconnectivity", "carconnectivity-connector-volkswagen"):' . "\n"
        . '    try: print(m.version(p))' . "\n"
        . '    except Exception: print("")'
    ) . ' 2>/dev/null', $ausgabe);
    return array(
        'kern'      => isset($ausgabe[0]) ? trim($ausgabe[0]) : '',
        'connector' => isset($ausgabe[1]) ? trim($ausgabe[1]) : '',
    );
}

/** Kurzform fuer die Anzeige: 'Kern 0.11.10 / Connector 0.10.6' oder ''. */
function vw_bibliothek_fassung()
{
    $f = vw_bibliothek_fassungen();
    if ($f['kern'] === '' && $f['connector'] === '') {
        return '';
    }
    return trim(($f['kern'] !== '' ? $f['kern'] : '?') . ' / '
              . ($f['connector'] !== '' ? $f['connector'] : '?'));
}

/** Fassung des Python in der virtuellen Umgebung, oder ''. */
function vw_python_fassung()
{
    $py = vw_paths()['bindir'] . '/venv/bin/python3';
    if (!is_file($py)) {
        return '';
    }
    $ausgabe = array();
    @exec(escapeshellcmd($py) . ' -c ' . escapeshellarg(
        'import sys; print("%d.%d.%d" % sys.version_info[:3])'
    ) . ' 2>/dev/null', $ausgabe);
    return trim(implode('', $ausgabe));
}

/** Ausgabe von vw.py --selbsttest. */
function vw_selbsttest()
{
    $p = vw_paths();
    $py = $p['bindir'] . '/venv/bin/python3';
    $skript = $p['bindir'] . '/vw.py';
    if (!is_file($py) || !is_file($skript)) {
        return "[FEHL] Die virtuelle Python-Umgebung oder vw.py fehlt.\n"
             . "       Erwartet: " . $py . "\n"
             . "                 " . $skript . "\n"
             . "       Abhilfe: Plugin neu installieren; die Installation legt beides an.";
    }
    $ausgabe = array();
    @exec(escapeshellcmd($py) . ' ' . escapeshellarg($skript) . ' --selbsttest 2>&1', $ausgabe);
    return implode("\n", $ausgabe);
}

/* ---------------- Befehlswarteschlange ----------------
 *
 * Sowohl der Miniserver-Endpunkt als auch der Reiter Test setzen Befehle ueber
 * diese eine Funktion ab. Zwei Kopien derselben Logik laufen zwangslaeufig
 * auseinander.
 *
 * Rueckgabe: array(ok, meldung). ok = 1 erledigt, 0 abgelehnt,
 * 2 eingereiht, aber ohne Antwort in der Wartezeit - also Ergebnis unbekannt.
 * Es wird bewusst kein Erfolg gemeldet, den niemand geprueft hat.
 */
function vw_befehl_absetzen($befehl, $wartezeit = null)
{
    $p = vw_paths();
    /* vw_config(FALSE) - diese Funktion wird auch vom unangemeldeten
     * Endpunkt gerufen.
     *
     * Gemessen am 03.09.2026 unter PHP 7.4 und 8.4: html/index.php liest die
     * Konfiguration ausdruecklich mit vw_config(false) ("NICHTS ANLEGEN"),
     * rief dann aber diese Funktion, und die holte sie mit der Vorgabe
     * $erzeugen = true noch einmal. Der Zwischenspeicher unterscheidet nach
     * 'j'/'n', der false-Aufruf schuetzte also nicht: bei fehlender oder
     * beschaedigter Datei schrieb der Endpunkt sie neu.
     *
     * Gebraucht wird die Konfiguration hier ohnehin nur, wenn der Aufrufer
     * keine Wartezeit nennt - der Endpunkt nennt immer eine. */
    if ($wartezeit === null) {
        $cfg = vw_config(false);
        $wartezeit = (int) $cfg['wartezeit'];
    }
    $wartezeit = max(0, min(30, (int) $wartezeit));

    $ordner = $p['datadir'] . '/befehle';
    if (!is_dir($ordner) && !@mkdir($ordner, 0775, true) && !is_dir($ordner)) {
        return array(0, 'Der Ordner fuer die Warteschlange liess sich nicht anlegen: ' . $ordner);
    }
    $kennung = bin2hex(random_bytes(8));
    $datei = $ordner . '/' . $kennung . '.json';
    $tmp = $datei . '.tmp';
    /* json_encode gibt bei ungueltigem UTF-8 false zurueck. file_put_contents
     * macht daraus eine leere Zeichenkette, schreibt null Byte und meldet das
     * als Erfolg - der Rueckgabewert ist 0, nicht false, die Pruefung auf
     * "=== false" greift also nicht, und rename schiebt die leere Datei in die
     * Warteschlange. Der Dienst faende dort einen Befehl, den er nicht deuten
     * kann. Deshalb zuerst kodieren und den Rueckgabewert ansehen - so, wie es
     * vw_config_write() weiter oben schon tut. */
    $vw_js = json_encode($befehl);
    if ($vw_js === false) {
        return array(0, 'Der Befehl liess sich nicht als JSON darstellen (ungueltiges UTF-8).');
    }
    if (@file_put_contents($tmp, $vw_js) !== strlen($vw_js) || !@rename($tmp, $datei)) {
        @unlink($tmp);
        return array(0, 'Der Befehl liess sich nicht ablegen: ' . $datei);
    }
    $antwort = $p['datadir'] . '/antworten/' . $kennung . '.json';
    for ($i = 0; $i < $wartezeit * 10; $i++) {
        if (is_file($antwort)) {
            $a = vw_json_lesen($antwort);
            /* Gelesen ist erledigt. Bis 0.9.0 blieb die Datei liegen; der
             * Dienst raeumt Antworten zwar beim naechsten Befehl weg, bis
             * dahin sammeln sie sich aber im Datenordner an - und jedes
             * Aufraeumen muss sie alle durchgehen. */
            @unlink($antwort);
            return array((int) (isset($a['ok']) ? $a['ok'] : 0),
                         (string) (isset($a['meldung']) ? $a['meldung'] : ''));
        }
        usleep(100000);
    }
    return array(2, 'Eingereiht, aber der Dienst hat innerhalb von ' . $wartezeit . ' s nicht geantwortet.');
}

/* ---------------- Verlauf ---------------- */

/** Messpunkte eines Tages: Array von array(ts, fuellstand, reichweite, km). */
function vw_verlauf_lesen($nummer, $tag = '')
{
    if ($tag === '') {
        $tag = date('Ymd');
    }
    if (!preg_match('/^[0-9]{8}$/', (string) $tag)) {
        return array();
    }
    $f = vw_paths()['datadir'] . '/verlauf/fahrzeug' . (int) $nummer . '_' . $tag . '.csv';
    $out = array();
    if (is_file($f)) {
        foreach (file($f, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: array() as $zeile) {
            $c = explode(';', $zeile);
            if (count($c) >= 2) {
                $out[] = array((int) $c[0], (float) $c[1],
                               isset($c[2]) && $c[2] !== '' ? (float) $c[2] : 0,
                               isset($c[3]) && $c[3] !== '' ? (float) $c[3] : 0);
            }
        }
    }
    return $out;
}

/**
 * Welche Tage liegen fuer dieses Fahrzeug vor? Neueste zuerst.
 *
 * Bis 0.9.9 zeigte die Oberflaeche nur den heutigen Tag, obwohl
 * vw_verlauf_lesen() schon einen Tag entgegennahm und der Dienst bis zu
 * neunzig Tage aufbewahrt. Ein Wert, der da ist und den niemand sehen kann,
 * ist nicht vorhanden.
 */
function vw_verlauf_tage($nummer, $hoechstens = 14)
{
    $ordner = vw_paths()['datadir'] . '/verlauf';
    $tage = array();
    foreach (glob($ordner . '/fahrzeug' . (int) $nummer . '_*.csv') ?: array() as $f) {
        if (preg_match('/_([0-9]{8})\.csv$/', $f, $m)) {
            $tage[] = $m[1];
        }
    }
    rsort($tage);
    return array_slice($tage, 0, max(1, (int) $hoechstens));
}

/**
 * Die abgeschlossenen Ladevorgaenge, neueste zuerst.
 *
 * Der Dienst schreibt sie; die Oberflaeche und der Endpunkt lesen nur. Alles,
 * was ZWEI Momentaufnahmen braucht, ist aus einem Seitenaufbau grundsaetzlich
 * nicht erreichbar - der Takt ist die einzige Stelle, die den Zustand
 * fortschreibt.
 *
 * Spalten: fahrzeug;start;ende;soc_vor;soc_nach;kwh;km;quelle
 */
/**
 * Wie viele Ladevorgaenge stehen fuer dieses Fahrzeug im Protokoll?
 *
 * Gezaehlt wird die DATEI, nicht die gelesene Liste. Bis 0.9.11 gab der
 * Endpunkt 'count($vw_l)' aus, und $vw_l war mit 400 gedeckelt: ab der
 * 401. Ladung meldete LADUNGEN dauerhaft 400, waehrend das MQTT-Thema
 * fahrzeugN/ladungen_gesamt weiterzaehlte. Beide Felder tragen dieselbe
 * Beschriftung und denselben Wertebereich - zwei Zahlen fuer dieselbe Sache,
 * die ab einem gewissen Punkt auseinanderlaufen.
 */
function vw_ladungen_zahl($nummer = 0)
{
    $f = vw_paths()['ladungen'];
    if (!is_file($f)) {
        return 0;
    }
    $n = 0;
    foreach (file($f, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: array() as $zeile) {
        if ($zeile === '' || $zeile[0] === '#') {
            continue;
        }
        $c = explode(';', $zeile);
        if (count($c) < 6) {
            continue;
        }
        if ($nummer > 0 && (int) $c[0] !== (int) $nummer) {
            continue;
        }
        $n++;
    }
    return $n;
}

function vw_ladungen_lesen($nummer = 0, $hoechstens = 200)
{
    $f = vw_paths()['ladungen'];
    if (!is_file($f)) {
        return array();
    }
    $out = array();
    foreach (file($f, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: array() as $zeile) {
        if ($zeile === '' || $zeile[0] === '#') {
            continue;
        }
        $c = explode(';', $zeile);
        if (count($c) < 6) {
            continue;
        }
        if ($nummer > 0 && (int) $c[0] !== (int) $nummer) {
            continue;
        }
        $out[] = array(
            'fahrzeug' => (int) $c[0],
            'start'    => (int) $c[1],
            'ende'     => (int) $c[2],
            'soc_vor'  => $c[3] !== '' ? (float) $c[3] : null,
            'soc_nach' => $c[4] !== '' ? (float) $c[4] : null,
            'kwh'      => $c[5] !== '' ? (float) $c[5] : null,
            'km'       => isset($c[6]) && $c[6] !== '' ? (float) $c[6] : null,
            'quelle'   => isset($c[7]) ? $c[7] : '',
        );
    }
    $out = array_reverse($out);
    return array_slice($out, 0, max(1, (int) $hoechstens));
}

/**
 * Entfernung zweier Punkte in Metern (Haversine, Erdradius 6371000 m).
 *
 * Steht in PHP UND in Python, weil es ueber die Sprachgrenze hinweg keine
 * gemeinsame Funktion gibt. Beide Fassungen rechnen dieselbe Formel; die
 * Selbstpruefung haelt sie gegeneinander.
 */
function vw_entfernung_m($b1, $l1, $b2, $l2)
{
    if (!is_numeric($b1) || !is_numeric($l1) || !is_numeric($b2) || !is_numeric($l2)) {
        return null;
    }
    $r = 6371000.0;
    $p1 = deg2rad((float) $b1);
    $p2 = deg2rad((float) $b2);
    $dp = deg2rad((float) $b2 - (float) $b1);
    $dl = deg2rad((float) $l2 - (float) $l1);
    $a = sin($dp / 2) * sin($dp / 2)
       + cos($p1) * cos($p2) * sin($dl / 2) * sin($dl / 2);
    return (int) round($r * 2 * atan2(sqrt($a), sqrt(max(0.0, 1 - $a))));
}

/* ---------------- MQTT-Gateway ----------------
 *
 * Das MQTT-Gateway ist seit LoxBerry 3 Bestandteil des Systems, kein Plugin.
 * Es wird nicht nachinstalliert, sondern unter System -> MQTT Gateway
 * eingeschaltet.
 *
 * Mqtt.Brokerhost ist ab Werk auf 'localhost' gesetzt. Eine Pruefung darauf
 * beantwortet also NICHT die Frage, ob Nachrichten ankommen koennen.
 * Massgeblich ist Gatewayautostart.
 */
function vw_mqtt_zustand()
{
    $p = vw_paths();
    $leer = array('gefunden' => 0, 'autostart' => 0, 'fassung' => 0, 'udpport' => 0,
                  'broker' => '', 'brokerport' => '', 'websocket' => '');
    if ($p['home'] === '') {
        return $leer;
    }
    $gen = vw_json_lesen($p['home'] . '/config/system/general.json');
    $m = array();
    if (isset($gen['Mqtt']) && is_array($gen['Mqtt'])) {
        $m = $gen['Mqtt'];
    } elseif (isset($gen['mqtt']) && is_array($gen['mqtt'])) {
        $m = $gen['mqtt'];
    }
    if (!$m) {
        return $leer;
    }
    $hol = function ($gross, $klein) use ($m) {
        if (isset($m[$gross])) {
            return $m[$gross];
        }
        return isset($m[$klein]) ? $m[$klein] : '';
    };
    $auto = $hol('Gatewayautostart', 'gatewayautostart');
    return array(
        'gefunden'   => 1,
        'autostart'  => in_array((string) $auto, array('1', 'true'), true) ? 1 : 0,
        /* Die FASSUNG des MQTT-Gateways, ab Werk 1. Sie entscheidet, was der
         * Anwender eintragen muss: unter V1 jedes Thema von Hand, ab V2
         * erscheint die Themengruppe von selbst in den Subscriptions.
         * 0 heisst "nicht feststellbar" - dann wird nichts behauptet,
         * sondern es werden beide Faelle genannt. */
        'fassung'    => (int) $hol('Gatewayversion', 'gatewayversion'),
        'udpport'    => (int) $hol('Udpinport', 'udpinport'),
        'broker'     => (string) $hol('Brokerhost', 'brokerhost'),
        'brokerport' => (string) $hol('Brokerport', 'brokerport'),
        'websocket'  => (string) $hol('Websocketport', 'websocketport'),
    );
}

/**
 * Der Hinweis zum MQTT-Abo - in der Fassung, die zum GATEWAY passt.
 *
 * Bis hierher stand an den Ausgabestellen unbedingt "Ohne diesen Eintrag
 * kommt am Miniserver nichts an". Das gilt fuer Gateway V1, wo jedes Thema
 * von Hand einzutragen ist. Ab V2 erscheint die Themengruppe von selbst in
 * den Subscriptions - der Satz schickte jeden V2-Anwender zu einem
 * Eingabeplatz, den es nicht gibt.
 *
 * Drei Ausgaenge, nicht zwei: ist die Fassung nicht feststellbar, werden
 * BEIDE Faelle genannt statt einer behauptet.
 */
function vw_abo_text()
{
    $m = vw_mqtt_zustand();
    $f = isset($m['fassung']) ? (int) $m['fassung'] : 0;
    if ($f <= 0) {
        return vw_t('MQTT.ABO_UNBEKANNT');
    }
    $gemessen = ' <span class="sm-mono">'
              . sprintf(vw_t('MQTT.ABO_GEMESSEN'), $f) . '</span>';
    return vw_t($f >= 2 ? 'MQTT.ABO_V2' : 'MQTT.ABO_WARNUNG') . $gemessen;
}


/**
 * Alle Themen, die der Dienst veroeffentlicht - mit Bedeutung, Einheit und
 * Wertebereich.
 *
 * Diese Tabelle ist zugleich die Anleitung und die Quelle der MQTT-Vorlage.
 * Sie muss zu MQTT_FELDER, MQTT_TEXTFELDER und MQTT_OBEN in bin/vw.py passen;
 * eine Zeile im Reiter Test zaehlt das bei jedem Seitenaufbau nach. Eine
 * Liste, die niemand nachmisst, laeuft auseinander - bei Renault stand sie
 * vier Stellen weit falsch, und keine Leseprufung hat es gefunden.
 *
 * Je Thema:
 *   s     Sprachschluessel der Bedeutung
 *   e     Einheit (fuer die Vorlage)
 *   a     1 = analog, 0 = digital
 *   min   Kleinstwert (negativ schaltet Signed="true")
 *   max   Groesstwert
 *   text  1 = Zeichenkette; bekommt KEINEN virtuellen Eingang
 */
function vw_mqtt_themen()
{
    $z = function ($s, $e, $a, $min, $max) {
        return array('s' => $s, 'e' => $e, 'a' => $a, 'min' => $min, 'max' => $max);
    };
    $t = function ($s) {
        return array('s' => $s, 'e' => '', 'a' => 0, 'min' => 0, 'max' => 0, 'text' => 1);
    };
    return array(
        // ---- oberhalb der Fahrzeugebene ----
        'ok'          => $z('VW_MQTT.OK', '', 0, 0, 1),
        'fahrzeuge'   => $z('VW_MQTT.FAHRZEUGE', '', 1, 0, 99),
        'ts'          => $z('VW_MQTT.TS', 's', 1, 0, 2147483647),
        'zaehler'     => $z('VW_MQTT.ZAEHLER', '', 1, -1, 999),
        'fehler_folge' => $z('VW_MQTT.FEHLER_FOLGE', '', 1, 0, 100000),
        'fehlertext'  => $t('VW_MQTT.FEHLERTEXT'),
        // ---- je Fahrzeug: Zahlen (bis 0.9.9) ----
        'fahrzeugN/soc'               => $z('VW_MQTT.SOC', '%', 1, 0, 100),
        'fahrzeugN/tank_prozent'      => $z('VW_MQTT.TANK', '%', 1, 0, 100),
        'fahrzeugN/reichweite_km'     => $z('VW_MQTT.REICHWEITE', 'km', 1, 0, 2000),
        'fahrzeugN/kilometerstand'    => $z('VW_MQTT.KM', 'km', 1, 0, 2000000),
        'fahrzeugN/verriegelt'        => $z('VW_MQTT.VERRIEGELT', '', 0, 0, 1),
        'fahrzeugN/tueren_offen'      => $z('VW_MQTT.TUEREN', '', 0, 0, 1),
        'fahrzeugN/fenster_offen'     => $z('VW_MQTT.FENSTER', '', 0, 0, 1),
        'fahrzeugN/licht_an'          => $z('VW_MQTT.LICHT', '', 0, 0, 1),
        'fahrzeugN/handbremse'        => $z('VW_MQTT.HANDBREMSE', '', 0, 0, 1),
        'fahrzeugN/zustand'           => $z('VW_MQTT.ZUSTAND', '', 1, 0, 3),
        'fahrzeugN/erreichbar'        => $z('VW_MQTT.ERREICHBAR', '', 0, 0, 1),
        'fahrzeugN/klima_an'          => $z('VW_MQTT.KLIMA', '', 0, 0, 1),
        'fahrzeugN/zieltemperatur'    => $z('VW_MQTT.ZIELTEMP', '&deg;C', 1, 0, 40),
        'fahrzeugN/aussentemperatur'  => $z('VW_MQTT.AUSSEN', '&deg;C', 1, -50, 60),
        'fahrzeugN/scheibenheizung'   => $z('VW_MQTT.SCHEIBE', '', 0, 0, 1),
        'fahrzeugN/laedt'             => $z('VW_MQTT.LAEDT', '', 0, 0, 1),
        'fahrzeugN/ladeleistung_kw'   => $z('VW_MQTT.LADEKW', 'kW', 1, 0, 400),
        'fahrzeugN/ladetempo_kmh'     => $z('VW_MQTT.TEMPO', 'km/h', 1, 0, 2000),
        'fahrzeugN/ladegrenze'        => $z('VW_MQTT.LADEGRENZE', '%', 1, 0, 100),
        'fahrzeugN/ladestrom_a'       => $z('VW_MQTT.LADESTROM', 'A', 1, 0, 64),
        'fahrzeugN/kabel_verbunden'   => $z('VW_MQTT.KABEL', '', 0, 0, 1),
        'fahrzeugN/stecker_verriegelt' => $z('VW_MQTT.STECKER', '', 0, 0, 1),
        'fahrzeugN/laden_fertig_um'   => $z('VW_MQTT.FERTIG', 's', 1, 0, 2147483647),
        'fahrzeugN/breite'            => $z('VW_MQTT.BREITE', '&deg;', 1, -90, 90),
        'fahrzeugN/laenge'            => $z('VW_MQTT.LAENGE', '&deg;', 1, -180, 180),
        'fahrzeugN/inspektion_tage'   => $z('VW_MQTT.INSP_TAGE', 'd', 1, -3650, 3650),
        'fahrzeugN/inspektion_km'     => $z('VW_MQTT.INSP_KM', 'km', 1, -200000, 200000),
        'fahrzeugN/oelservice_tage'   => $z('VW_MQTT.OEL_TAGE', 'd', 1, -3650, 3650),
        'fahrzeugN/oelservice_km'     => $z('VW_MQTT.OEL_KM', 'km', 1, -200000, 200000),
        // ---- je Fahrzeug: Zahlen, ab 0.9.10 ----
        'fahrzeugN/reichweite_elektro_km'   => $z('VW_MQTT.REICHW_E', 'km', 1, 0, 2000),
        'fahrzeugN/reichweite_verbrenner_km' => $z('VW_MQTT.REICHW_V', 'km', 1, 0, 2000),
        'fahrzeugN/reichweite_wltp_km'      => $z('VW_MQTT.REICHW_WLTP', 'km', 1, 0, 2000),
        'fahrzeugN/batterie_kwh'            => $z('VW_MQTT.BATT_KWH', 'kWh', 1, 0, 300),
        'fahrzeugN/batterie_temp'           => $z('VW_MQTT.BATT_TEMP', '&deg;C', 1, -50, 90),
        'fahrzeugN/oelstand_prozent'        => $z('VW_MQTT.OELSTAND', '%', 1, 0, 100),
        'fahrzeugN/anzahl_antriebe'         => $z('VW_MQTT.ANTRIEBE', '', 1, 0, 4),
        'fahrzeugN/klima_fertig_um'         => $z('VW_MQTT.KLIMA_FERTIG', 's', 1, 0, 2147483647),
        'fahrzeugN/sitzheizung_ein'         => $z('VW_MQTT.SITZHEIZUNG', '', 0, 0, 1),
        'fahrzeugN/klima_bei_entriegeln'    => $z('VW_MQTT.KLIMA_ENTR', '', 0, 0, 1),
        'fahrzeugN/stecker_entriegeln'      => $z('VW_MQTT.STECKER_AUTO', '', 0, 0, 1),
        'fahrzeugN/verbrauch'               => $z('VW_MQTT.VERBRAUCH', 'kWh/100km', 1, 0, 200),
        'fahrzeugN/adblue_km'               => $z('VW_MQTT.ADBLUE', 'km', 1, 0, 20000),
        'fahrzeugN/tueren_zahl'             => $z('VW_MQTT.TUEREN_ZAHL', '', 1, 0, 10),
        'fahrzeugN/fenster_zahl'            => $z('VW_MQTT.FENSTER_ZAHL', '', 1, 0, 10),
        'fahrzeugN/standzeit_min'           => $z('VW_MQTT.STANDZEIT', 'min', 1, 0, 2147483647),
        'fahrzeugN/hoehe'                   => $z('VW_MQTT.HOEHE', 'm', 1, -500, 9000),
        'fahrzeugN/entfernung_m'            => $z('VW_MQTT.ENTFERNUNG', 'm', 1, 0, 40000000),
        'fahrzeugN/zuhause'                 => $z('VW_MQTT.ZUHAUSE', '', 0, 0, 1),
        'fahrzeugN/ladesaeule_kw'           => $z('VW_MQTT.SAEULE_KW', 'kW', 1, 0, 400),
        'fahrzeugN/ladeempfehlung'          => $z('VW_MQTT.EMPFEHLUNG', '', 1, -1, 1),
        'fahrzeugN/ladung_kwh'              => $z('VW_MQTT.LADUNG_KWH', 'kWh', 1, 0, 300),
        'fahrzeugN/ladung_dauer_min'        => $z('VW_MQTT.LADUNG_MIN', 'min', 1, 0, 100000),
        'fahrzeugN/ladung_vor_stunden'      => $z('VW_MQTT.LADUNG_VOR', 'h', 1, 0, 100000),
        'fahrzeugN/tag_kwh'                 => $z('VW_MQTT.TAG_KWH', 'kWh', 1, 0, 1000),
        'fahrzeugN/ladungen_gesamt'         => $z('VW_MQTT.LADUNGEN', '', 1, 0, 100000),
        // ---- je Fahrzeug: Text, ab 0.9.10 ----
        'fahrzeugN/zustand_text'            => $t('VW_MQTT.T_ZUSTAND'),
        'fahrzeugN/klima_text'              => $t('VW_MQTT.T_KLIMA'),
        'fahrzeugN/ladezustand_text'        => $t('VW_MQTT.T_LADEN'),
        'fahrzeugN/ladeart'                 => $t('VW_MQTT.T_LADEART'),
        'fahrzeugN/externe_stromversorgung' => $t('VW_MQTT.T_EXTERN'),
        'fahrzeugN/positionsart'            => $t('VW_MQTT.T_POSART'),
        'fahrzeugN/adresse'                 => $t('VW_MQTT.T_ADRESSE'),
        'fahrzeugN/modell'                  => $t('VW_MQTT.T_MODELL'),
        'fahrzeugN/vin'                     => $t('VW_MQTT.T_VIN'),
        'fahrzeugN/kennzeichen'             => $t('VW_MQTT.T_KENNZEICHEN'),
        'fahrzeugN/software'                => $t('VW_MQTT.T_SOFTWARE'),
        'fahrzeugN/tueren_namen'            => $t('VW_MQTT.T_TUEREN_NAMEN'),
        'fahrzeugN/fenster_namen'           => $t('VW_MQTT.T_FENSTER_NAMEN'),
        'fahrzeugN/ladesaeule_name'         => $t('VW_MQTT.T_SAEULE_NAME'),
        'fahrzeugN/ladesaeule_betreiber'    => $t('VW_MQTT.T_SAEULE_BETREIBER'),
        'fahrzeugN/ausfalltext'             => $t('VW_MQTT.T_AUSFALL'),
    );
}

/**
 * Die Themennamen, die der DIENST wirklich sendet - aus bin/vw.py gelesen.
 *
 * Statisch gelesen, nicht ausgefuehrt. Rueckgabe:
 * array('felder' => [...], 'text' => [...], 'oben' => [...]) oder null, wenn
 * die Datei nicht auffindbar oder das Muster nicht zu finden ist. null heisst
 * "nicht gemessen" und darf nicht wie "in Ordnung" aussehen.
 */
function vw_mqtt_themen_im_dienst()
{
    $p = vw_paths();
    $kandidaten = array(
        $p['bindir'] . '/vw.py',
        dirname(dirname(dirname(__FILE__))) . '/bin/vw.py',
    );
    $t = '';
    foreach ($kandidaten as $k) {
        if ($k !== '' && is_file($k)) {
            $t = (string) @file_get_contents($k);
            break;
        }
    }
    if ($t === '') {
        return null;
    }
    $aus = array();
    foreach (array('felder' => 'MQTT_FELDER', 'text' => 'MQTT_TEXTFELDER',
                   'oben' => 'MQTT_OBEN') as $name => $konstante) {
        if (!preg_match('/^' . $konstante . '\s*=\s*\((.*?)^\)/ms', $t, $m)) {
            return null;
        }
        preg_match_all('/"([a-z0-9_]+)"/', $m[1], $x);
        $aus[$name] = $x[1];
    }
    return $aus;
}

/* ==================================================================
 * Loxone-Vorlagen
 *
 * Nachbau der Bausteine aus LoxBerry::LoxoneTemplateBuilder; das Modul gibt es
 * nur in Perl. Attributreihenfolge, CRLF als Zeilenende und der Tabulator vor
 * den Kindelementen entsprechen dem Original. Wortgleich uebernommen aus
 * LoxBerry-Plugin-APC-UPS-1.0.0 (ap_xml_virtual_in_http) - nicht neu
 * geschrieben, weil die Fassung dort geprueft ist.
 * ================================================================== */

function vw_x($s)
{
    return htmlspecialchars((string) $s, ENT_QUOTES | ENT_XML1, 'UTF-8');
}

/**
 * Ein virtueller HTTP-Eingang.
 *
 * Stand des Hausstandards vom 25.08.2026, gemessen gegen die Ausfuhren aus der
 * laufenden Anlage und gegen XML_Vorlagen_0.9.10. Bis 0.9.9 fehlten dem
 * Nachbau drei Dinge, die 35 Plugin-Ordner im Bestand fuehren:
 *
 *   HintText="" am Wurzelelement
 *   <Info templateType="2" minVersion="17010727"/> als ERSTES Kindelement
 *   Unit="<v.1> <Einheit>" und HintText="" je Eintrag
 *
 * Ein Verweis auf eine Vorlage altert mit, ohne dass man es der eigenen Datei
 * ansieht. Die billigste Pruefung ist ein Zaehlen im Bestand.
 *
 * Je Eintrag erwartet: title, comment, check, unit, analog, min, max.
 */
function vw_xml_virtual_in_http($kopf, $cmds)
{
    $crlf = "\r\n";
    $o = '<?xml version="1.0" encoding="utf-8"?>' . $crlf;
    $o .= '<VirtualInHttp ';
    $o .= 'HintText="" ';
    $o .= 'Title="' . vw_x($kopf['title']) . '" ';
    $o .= 'Comment="' . vw_x(isset($kopf['comment']) ? $kopf['comment'] : '') . '" ';
    $o .= 'Address="' . vw_x(isset($kopf['address']) ? $kopf['address'] : '') . '" ';
    $o .= 'PollingTime="' . vw_x(isset($kopf['polling']) ? $kopf['polling'] : '60') . '"';
    $o .= '>' . $crlf;
    $o .= "\t" . '<Info templateType="2" minVersion="17010727"/>' . $crlf;
    foreach ($cmds as $c) {
        $einheit = isset($c['unit']) ? trim((string) $c['unit']) : '';
        $min = isset($c['min']) ? (int) $c['min'] : -2147483647;
        $max = isset($c['max']) ? (int) $c['max'] : 2147483647;
        $analog = (!isset($c['analog']) || $c['analog']) ? 'true' : 'false';
        $o .= "\t" . '<VirtualInHttpCmd ';
        $o .= 'Title="' . vw_x($c['title']) . '" ';
        $o .= 'Comment="' . vw_x(isset($c['comment']) ? $c['comment'] : '') . '" ';
        $o .= 'Check="' . vw_x(isset($c['check']) ? $c['check'] : ' ') . '" ';
        $o .= 'Signed="' . ($min < 0 ? 'true' : 'false') . '" ';
        $o .= 'Analog="' . $analog . '" ';
        $o .= 'SourceValLow="0" ';
        $o .= 'DestValLow="0" ';
        $o .= 'SourceValHigh="1" ';
        $o .= 'DestValHigh="1" ';
        $o .= 'DefVal="0" ';
        $o .= 'MinVal="' . $min . '" ';
        $o .= 'MaxVal="' . $max . '" ';
        $o .= 'Unit="' . vw_x('<v.1>' . ($einheit !== '' ? ' ' . $einheit : '')) . '" ';
        $o .= 'HintText=""';
        $o .= '/>' . $crlf;
    }
    $o .= '</VirtualInHttp>' . $crlf;
    return $o;
}

/**
 * Ein virtueller Ausgang - der Weg, auf dem Loxone SCHALTET.
 *
 * Bis 0.9.9 gab es ihn nicht: die elf schaltenden Adressen standen nur als
 * Tabelle zum Abschreiben in der Oberflaeche. 30 Plugins im Bestand erzeugen
 * die Datei; gezaehlt am 27.08.2026.
 *
 * Zwei Dinge, die man leicht falsch macht:
 *   - Der Titel eines Ausgangs darf kein '=' tragen. Aus '&lp=1' wurde durch
 *     blosses Ersetzen von '&' einmal der Name 'EVCC_MODUS_LP=1'.
 *   - CmdOffMethod und Repeat/RepeatRate gehoeren dazu, auch wenn es keinen
 *     Ausbefehl gibt - dann steht CmdOff leer.
 *
 * Je Eintrag erwartet: title, comment, on, off (leer erlaubt), analog.
 */
function vw_xml_virtual_out($kopf, $cmds)
{
    $crlf = "\r\n";
    $o = '<?xml version="1.0" encoding="utf-8"?>' . $crlf;
    $o .= '<VirtualOut ';
    $o .= 'HintText="" ';
    $o .= 'Title="' . vw_x($kopf['title']) . '" ';
    $o .= 'Comment="' . vw_x(isset($kopf['comment']) ? $kopf['comment'] : '') . '" ';
    $o .= 'Address="' . vw_x(isset($kopf['address']) ? $kopf['address'] : '') . '" ';
    $o .= 'CmdInit="" ';
    $o .= 'CloseAfterSend="false" ';
    $o .= 'CmdSep=""';
    $o .= '>' . $crlf;
    $o .= "\t" . '<Info templateType="3" minVersion="17010727"/>' . $crlf;
    foreach ($cmds as $c) {
        $o .= "\t" . '<VirtualOutCmd ';
        $o .= 'Title="' . vw_x(str_replace('=', ' ', (string) $c['title'])) . '" ';
        $o .= 'Comment="' . vw_x(isset($c['comment']) ? $c['comment'] : '') . '" ';
        $o .= 'CmdOnMethod="GET" ';
        $o .= 'CmdOn="' . vw_x(isset($c['on']) ? $c['on'] : '') . '" ';
        $o .= 'CmdOffMethod="GET" ';
        $o .= 'CmdOff="' . vw_x(isset($c['off']) ? $c['off'] : '') . '" ';
        $o .= 'Analog="' . (!empty($c['analog']) ? 'true' : 'false') . '" ';
        $o .= 'Repeat="0" ';
        $o .= 'RepeatRate="0" ';
        $o .= 'HintText=""';
        $o .= '/>' . $crlf;
    }
    $o .= '</VirtualOut>' . $crlf;
    return $o;
}

/**
 * Die Werte des Status-Endpunkts.
 *
 * Reihenfolge und Namen sind zugleich die Reihenfolge der Befehlserkennungen
 * in der Loxone-Vorlage. Wer hier etwas einfuegt, aendert die Vorlage mit.
 *
 * Je Feld: array(Einheit, Sprachschluessel, analog, Kleinstwert, Groesstwert).
 * Die letzten drei stehen hier, weil die Vorlage sie braucht: ein digitaler
 * Eingang mit MinVal -2147483647 ist zwar nicht falsch, aber in Loxone Config
 * unbrauchbar beschriftet. Ein negativer Kleinstwert schaltet zugleich
 * Signed="true" - INSPTAGE wird negativ, wenn die Inspektion faellig ist,
 * und das ist eine Aussage.
 *
 * NEUE FELDER WERDEN HINTEN ANGEHAENGT. Wer eines dazwischenschiebt,
 * verschiebt in jeder bestehenden Anlage die Zuordnung der Eingaenge.
 */
function vw_status_felder()
{
    return array(
        'SOC'        => array('%',      'VW_FELD.SOC',        1, 0, 100),
        'TANK'       => array('%',      'VW_FELD.TANK',       1, 0, 100),
        'REICHW'     => array('km',     'VW_FELD.REICHW',     1, 0, 2000),
        'KM'         => array('km',     'VW_FELD.KM',         1, 0, 2000000),
        'VERR'       => array('',       'VW_FELD.VERR',       0, 0, 1),
        'TUEREN'     => array('',       'VW_FELD.TUEREN',     0, 0, 1),
        'FENSTER'    => array('',       'VW_FELD.FENSTER',    0, 0, 1),
        'LICHT'      => array('',       'VW_FELD.LICHT',      0, 0, 1),
        'HANDBR'     => array('',       'VW_FELD.HANDBR',     0, 0, 1),
        'KLIMA'      => array('',       'VW_FELD.KLIMA',      0, 0, 1),
        'ZIELTEMP'   => array('&deg;C', 'VW_FELD.ZIELTEMP',   1, 0, 40),
        'AUSSEN'     => array('&deg;C', 'VW_FELD.AUSSEN',     1, -50, 60),
        'SCHEIBE'    => array('',       'VW_FELD.SCHEIBE',    0, 0, 1),
        'ZUSTAND'    => array('',       'VW_FELD.ZUSTAND',    1, 0, 3),
        'ERREICH'    => array('',       'VW_FELD.ERREICH',    0, 0, 1),
        'ALTER'      => array('s',      'VW_FELD.ALTER',      1, 0, 2147483647),
        'OK'         => array('',       'VW_FELD.OK',         0, 0, 1),
        // ---- ab 0.9.10 ----
        'ZAEHLER'    => array('',       'VW_FELD.ZAEHLER',    1, -1, 999),
        'FEHLFOLGE'  => array('',       'VW_FELD.FEHLFOLGE',  1, 0, 100000),
        'ZUHAUSE'    => array('',       'VW_FELD.ZUHAUSE',    0, 0, 1),
        'ENTFERNUNG' => array('m',      'VW_FELD.ENTFERNUNG', 1, 0, 40000000),
        'STANDZEIT'  => array('min',    'VW_FELD.STANDZEIT',  1, 0, 2147483647),
        'SITZHEIZ'   => array('',       'VW_FELD.SITZHEIZ',   0, 0, 1),
        'KLIMAENTR'  => array('',       'VW_FELD.KLIMAENTR',  0, 0, 1),
        'BATTTEMP'   => array('&deg;C', 'VW_FELD.BATTTEMP',   1, -50, 90),
        'VERBRAUCH'  => array('kWh/100km', 'VW_FELD.VERBRAUCH', 1, 0, 200),
        'REICHWWLTP' => array('km',     'VW_FELD.REICHWWLTP', 1, 0, 2000),
        'TUERENZAHL' => array('',       'VW_FELD.TUERENZAHL', 1, 0, 10),
        'FENSTERZAHL'=> array('',       'VW_FELD.FENSTERZAHL', 1, 0, 10),
    );
}

/** Die Werte des Lade-Endpunkts. */
function vw_laden_felder()
{
    return array(
        'SOC'        => array('%',    'VW_LFELD.SOC',        1, 0, 100),
        'LAEDT'      => array('',     'VW_LFELD.LAEDT',      0, 0, 1),
        'LADEKW'     => array('kW',   'VW_LFELD.LADEKW',     1, 0, 400),
        'TEMPO'      => array('km/h', 'VW_LFELD.TEMPO',      1, 0, 2000),
        'LADEGR'     => array('%',    'VW_LFELD.LADEGR',     1, 0, 100),
        'LADESTROM'  => array('A',    'VW_LFELD.LADESTROM',  1, 0, 64),
        'KABEL'      => array('',     'VW_LFELD.KABEL',      0, 0, 1),
        'STECKER'    => array('',     'VW_LFELD.STECKER',    0, 0, 1),
        'REICHWBAT'  => array('km',   'VW_LFELD.REICHWBAT',  1, 0, 2000),
        'FERTIGMIN'  => array('min',  'VW_LFELD.FERTIGMIN',  1, 0, 100000),
        'OK'         => array('',     'VW_LFELD.OK',         0, 0, 1),
        // ---- ab 0.9.10 ----
        'LADEART'    => array('',     'VW_LFELD.LADEART',    1, 0, 2),
        'STECKERAUTO'=> array('',     'VW_LFELD.STECKERAUTO', 0, 0, 1),
        'LADEMAXKW'  => array('kW',   'VW_LFELD.LADEMAXKW',  1, 0, 400),
        'BATTKWH'    => array('kWh',  'VW_LFELD.BATTKWH',    1, 0, 300),
        'BATTTEMP'   => array('&deg;C', 'VW_LFELD.BATTTEMP', 1, -50, 90),
        'EMPFEHLUNG' => array('',     'VW_LFELD.EMPFEHLUNG', 1, -1, 1),
        'ALTER'      => array('s',    'VW_LFELD.ALTER',      1, 0, 2147483647),
    );
}

/** Die Werte des Wartungs-Endpunkts. */
function vw_wartung_felder()
{
    return array(
        'INSPTAGE'  => array('d',   'VW_WFELD.INSPTAGE', 1, -3650, 3650),
        'INSPKM'    => array('km',  'VW_WFELD.INSPKM',   1, -200000, 200000),
        'OELTAGE'   => array('d',   'VW_WFELD.OELTAGE',  1, -3650, 3650),
        'OELKM'     => array('km',  'VW_WFELD.OELKM',    1, -200000, 200000),
        'KM'        => array('km',  'VW_WFELD.KM',       1, 0, 2000000),
        'OK'        => array('',    'VW_WFELD.OK',       0, 0, 1),
        // ---- ab 0.9.10 ----
        'ADBLUEKM'  => array('km',  'VW_WFELD.ADBLUEKM', 1, 0, 20000),
        'OELSTAND'  => array('%',   'VW_WFELD.OELSTAND', 1, 0, 100),
        'ALTER'     => array('s',   'VW_WFELD.ALTER',    1, 0, 2147483647),
    );
}

/** Die Werte des Positions-Endpunkts. */
function vw_position_felder()
{
    return array(
        'BREITE'     => array('&deg;', 'VW_PFELD.BREITE',     1, -90, 90),
        'LAENGE'     => array('&deg;', 'VW_PFELD.LAENGE',     1, -180, 180),
        'ENTFERNUNG' => array('m',     'VW_PFELD.ENTFERNUNG', 1, 0, 40000000),
        'ZUHAUSE'    => array('',      'VW_PFELD.ZUHAUSE',    0, 0, 1),
        'HOEHE'      => array('m',     'VW_PFELD.HOEHE',      1, -500, 9000),
        'OK'         => array('',      'VW_PFELD.OK',         0, 0, 1),
        'ALTER'      => array('s',     'VW_PFELD.ALTER',      1, 0, 2147483647),
    );
}

/** Die Werte des Verbrauchs-Endpunkts (Ladebilanz und Fahrverbrauch). */
function vw_verbrauch_felder()
{
    return array(
        'VERBRAUCH'   => array('kWh/100km', 'VW_VFELD.VERBRAUCH',  1, 0, 200),
        'LETZTKWH'    => array('kWh',  'VW_VFELD.LETZTKWH',   1, 0, 300),
        'LETZTMIN'    => array('min',  'VW_VFELD.LETZTMIN',   1, 0, 100000),
        'LETZTVOR'    => array('%',    'VW_VFELD.LETZTVOR',   1, 0, 100),
        'LETZTNACH'   => array('%',    'VW_VFELD.LETZTNACH',  1, 0, 100),
        'LETZTVORSTD' => array('h',    'VW_VFELD.LETZTVORSTD', 1, 0, 100000),
        'TAGKWH'      => array('kWh',  'VW_VFELD.TAGKWH',     1, 0, 1000),
        'LADUNGEN'    => array('',     'VW_VFELD.LADUNGEN',   1, 0, 100000),
        'OK'          => array('',     'VW_VFELD.OK',         0, 0, 1),
        'ALTER'       => array('s',    'VW_VFELD.ALTER',      1, 0, 2147483647),
    );
}

/**
 * Die schaltenden Befehle - an EINER Stelle.
 *
 * Sie speist drei Verbraucher: die Positivliste des Endpunkts, die Tabelle im
 * Reiter "Einbindung in Loxone" und die erzeugte Ausgangsvorlage. Bis 0.9.9
 * standen acht von elf Befehlen in der Tabelle; 'zieltemperatur' kam in der
 * ganzen Oberflaeche nicht vor, obwohl der Endpunkt ihn annahm.
 *
 * Je Eintrag:
 *   schluessel   Sprachschluessel fuer die Bezeichnung
 *   gegen        der Ausbefehl, mit dem zusammen ein EIN/AUS-Ausgang entsteht
 *   param        zusaetzlicher Adressteil, '<v>' wird von Loxone ersetzt
 *   eingreifend  1 = braucht den zweiten Haken (bewegt oder oeffnet das Fahrzeug)
 *   spin         1 = ohne hinterlegte S-PIN nicht moeglich
 */
function vw_befehle()
{
    return array(
        'klima_start'    => array('s' => 'VW_BEF.KLIMA_START',  'gegen' => 'klima_stop',
                                  'param' => '&temp=<v>', 'analog' => 1),
        'klima_stop'     => array('s' => 'VW_BEF.KLIMA_STOP'),
        'zieltemperatur' => array('s' => 'VW_BEF.ZIELTEMP',     'param' => '&temp=<v>',
                                  'analog' => 1),
        'laden_start'    => array('s' => 'VW_BEF.LADEN_START',  'gegen' => 'laden_stop'),
        'laden_stop'     => array('s' => 'VW_BEF.LADEN_STOP'),
        'ladegrenze'     => array('s' => 'VW_BEF.LADEGRENZE',   'param' => '&prozent=<v>',
                                  'analog' => 1),
        'ladestrom'      => array('s' => 'VW_BEF.LADESTROM',    'param' => '&ampere=<v>',
                                  'analog' => 1),
        'scheibe_ein'    => array('s' => 'VW_BEF.SCHEIBE_EIN',  'gegen' => 'scheibe_aus'),
        'scheibe_aus'    => array('s' => 'VW_BEF.SCHEIBE_AUS'),
        'wecken'         => array('s' => 'VW_BEF.WECKEN'),
        'abruf'          => array('s' => 'VW_BEF.ABRUF'),
        // ---- ab 0.9.10 ----
        'entriegeln'     => array('s' => 'VW_BEF.ENTRIEGELN',   'gegen' => 'verriegeln',
                                  'eingreifend' => 1, 'spin' => 1),
        'verriegeln'     => array('s' => 'VW_BEF.VERRIEGELN',   'eingreifend' => 1, 'spin' => 1),
        'blinken'        => array('s' => 'VW_BEF.BLINKEN',      'eingreifend' => 1),
        'hupen'          => array('s' => 'VW_BEF.HUPEN',        'eingreifend' => 1),
        'einstellung'    => array('s' => 'VW_BEF.EINSTELLUNG',
                                  'param' => '&name=sitzheizung&wert=<v>', 'analog' => 1),
    );
}

/**
 * Die Ja/Nein-Einstellungen, die 'einstellung&name=...' setzen kann.
 *
 * Alle vier liest der Dienst bereits ab; gesetzt wurden sie bis 0.9.9 nicht.
 * Ob der Volkswagen-Connector fuer jede einen Schreibhaken registriert, ist
 * UNGEMESSEN - hier liegt kein Fahrzeug. Der Dienst weist deshalb sauber ab,
 * statt zu raten.
 */
function vw_schalter()
{
    return array(
        'sitzheizung'      => 'VW_SCHALT.SITZHEIZUNG',
        'klima_entriegeln' => 'VW_SCHALT.KLIMA_ENTRIEGELN',
        'stecker_auto'     => 'VW_SCHALT.STECKER_AUTO',
        'klima_ohne_netz'  => 'VW_SCHALT.KLIMA_OHNE_NETZ',
    );
}

/**
 * Der Suchtext eines Feldes fuer den virtuellen Eingang in Loxone.
 *
 * Das Semikolon gehoert DAZU. Ohne es nimmt Loxone die erste Fundstelle,
 * und die kann zu einem anderen Feld gehoeren, dessen Name auf diesen
 * endet. Gemessen an der Antwort des Wartungs-Endpunkts: das Muster
 * \iKM= trifft dort INSPKM=15000, nicht KM=48210. Beide Zahlen sehen aus
 * wie ein Kilometerstand - der Fehler faellt an keiner Stelle auf.
 *
 * Und es gibt diese Funktion, damit der Suchtext an EINER Stelle
 * entsteht. Vorher stand er fuenfmal woertlich da: einmal in der Vorlage
 * und viermal in der Oberflaeche. Vier Kopien einer Regel sind vier
 * Gelegenheiten, sie an einer Stelle zu vergessen.
 */
function vw_check($feld)
{
    return '\i;' . $feld . '=\i\v';
}

/** Der Rechnername fuer die erzeugten Adressen. */
function vw_host()
{
    return isset($_SERVER['HTTP_HOST']) && $_SERVER['HTTP_HOST'] !== ''
        ? preg_replace('/[^A-Za-z0-9\.\-:]/', '', (string) $_SERVER['HTTP_HOST'])
        : (gethostname() ?: 'loxberry');
}

/**
 * Die Feldliste zu einer Vorlagenart. Leer, wenn die Art unbekannt ist.
 *
 * Eine Stelle, an der Art und Feldliste zusammenfinden - sonst muss jede der
 * vier Erzeugungen die Zuordnung wiederholen.
 */
function vw_felder_zu_art($art)
{
    switch ($art) {
        case 'status':    return vw_status_felder();
        case 'laden':     return vw_laden_felder();
        case 'wartung':   return vw_wartung_felder();
        case 'position':  return vw_position_felder();
        case 'verbrauch': return vw_verbrauch_felder();
    }
    return array();
}

/**
 * Das Namenskuerzel einer Eingangsvorlage.
 *
 * Die STATUSVORLAGE traegt die blanken Namen (VW_1_SOC) - sie ist die, die
 * fast jeder zuerst einliest, und ihre Namen bleiben unveraendert. Jede
 * WEITERE Vorlage bekommt ihr eigenes Kuerzel (VW_1_LD_SOC).
 *
 * Der Grund, gemessen am 03.09.2026: die fuenf Vorlagen erzeugten zusammen
 * 73 Eingaenge unter nur 59 verschiedenen Namen. VW_1_OK und VW_1_ALTER
 * standen fuenfmal da, VW_1_SOC, VW_1_KM, VW_1_BATTTEMP, VW_1_ENTFERNUNG,
 * VW_1_ZUHAUSE und VW_1_VERBRAUCH je zweimal. Wer zwei Vorlagen einliest,
 * bekommt gleichnamige Befehlserkennungen, und die Baustein-Liste dieses
 * Plugins nennt Namen wie VW_1_ALTER, ohne sagen zu koennen, welcher
 * gemeint ist.
 *
 * Ein Kuerzel je Vorlage loest das ein fuer alle Mal - auch fuer Felder,
 * die erst spaeter dazukommen.
 */
function vw_vorlagenkuerzel($art)
{
    $k = array('status' => '', 'laden' => 'LD_', 'wartung' => 'WA_',
               'position' => 'PO_', 'verbrauch' => 'VB_');
    return isset($k[$art]) ? $k[$art] : '';
}

/** Die Arten, fuer die es eine Eingangsvorlage gibt. */
function vw_vorlagenarten()
{
    return array('status', 'laden', 'wartung', 'position', 'verbrauch');
}

/**
 * Vorlage fuer den Import in Loxone Config. Rueckgabe: array(name, inhalt)
 *
 * Bis 0.9.9 gab es sie nur fuer den Status-Endpunkt und nur fuer Fahrzeug 1,
 * mit einem fest verdrahteten Zyklus von 300 s. Die uebrigen drei Endpunkte
 * mussten von Hand abgetippt werden, obwohl ihre Feldlisten fertig danebenlagen.
 *
 * Der Zyklus kommt jetzt aus dem eingestellten Takt: ein Miniserver, der
 * haeufiger fragt als der Dienst abruft, bekommt nur denselben Wert noch
 * einmal - und einer, der seltener fragt, verschenkt Aktualitaet.
 */
function vw_vorlage($nummer = 1, $art = 'status')
{
    $p = vw_paths();
    $cfg = vw_config();
    $felder = vw_felder_zu_art($art);
    if (!$felder) {
        $art = 'status';
        $felder = vw_status_felder();
    }
    $host = vw_host();
    $token = vw_token();
    $kuerzel = vw_vorlagenkuerzel($art);
    $cmds = array();
    foreach ($felder as $feld => $info) {
        // Der Text laeuft gleich durch vw_x() und wuerde dort ein zweites Mal
        // maskiert. Deshalb erst Auszeichnung entfernen und Entitaeten
        // aufloesen - sonst stuende in Loxone Config wortwoertlich
        // 'l&auml;dt' statt 'laedt'.
        $bedeutung = trim(strip_tags(html_entity_decode(vw_t($info[1]), ENT_QUOTES, 'UTF-8')));
        $einheit = trim(strip_tags(html_entity_decode($info[0], ENT_QUOTES, 'UTF-8')));
        $cmds[] = array(
            'title'   => 'VW_' . (int) $nummer . '_' . $kuerzel . $feld,
            'comment' => $bedeutung . ($einheit !== '' ? ' [' . $einheit . ']' : ''),
            'check'   => vw_check($feld),
            'unit'    => $einheit,
            'analog'  => isset($info[2]) ? $info[2] : 1,
            'min'     => isset($info[3]) ? $info[3] : -2147483647,
            'max'     => isset($info[4]) ? $info[4] : 2147483647,
        );
    }
    $adresse = 'http://' . $host . '/plugins/' . $p['plugin']
             . '/index.php?token=' . $token . '&aktion=' . $art . '&fahrzeug=' . (int) $nummer;
    return array(
        'volkswagen_fahrzeug' . (int) $nummer . '_' . $art . '.xml',
        vw_xml_virtual_in_http(array(
            'title'   => 'Volkswagen ' . (int) $nummer . ' ' . vw_t('LOX.ART_' . strtoupper($art)),
            'address' => $adresse,
            /* Die Wartungsdaten holt der Dienst nur jeden N-ten Takt
             * (takt_wartung, ab Werk 12). Ein Miniserver, der sie im
             * Abruftakt abfragt, bekommt zwoelfmal denselben Wert - genau
             * das, was der Doc-Block oben ausschliessen will. Bis 0.9.11
             * trugen alle fuenf Vorlagen denselben Zyklus. */
            'polling' => (string) ($art === 'wartung'
                ? max(60, (int) $cfg['intervall'] * max(1, (int) $cfg['takt_wartung']))
                : max(60, (int) $cfg['intervall'])),
            'comment' => 'Erzeugt vom LoxBerry-Plugin Volkswagen ID (' . date('d.m.Y') . ')',
        ), $cmds),
    );
}

/**
 * Die Ausgangsvorlage: alle schaltenden Befehle als virtuelle Ausgaenge.
 *
 * Zusammengehoerende Befehle werden zu EINEM Ausgang: 'klima_start' bekommt
 * 'klima_stop' als Ausbefehl. Das ist die Form, die in Loxone einen Schalter
 * ergibt statt zweier Taster.
 *
 * Eingreifende Befehle - Ver- und Entriegeln, Hupe, Lichthupe - kommen nur in
 * die Datei, wenn der zweite Haken gesetzt ist. Ein Ausgang, der eine gesperrte
 * Adresse anspricht, bekommt HTTP 403, und ein Virtueller Ausgang wertet die
 * Antwort nicht aus: der Anwender saehe einen Schalter, der nichts tut und
 * nichts sagt.
 */
function vw_vorlage_vo($nummer = 1)
{
    $p = vw_paths();
    $cfg = vw_config();
    $host = vw_host();
    $token = vw_token();
    $basis = '/plugins/' . $p['plugin'] . '/index.php?token=' . $token;
    $alle = vw_befehle();
    $cmds = array();
    $verbraucht = array();

    foreach ($alle as $name => $b) {
        if (isset($verbraucht[$name])) {
            continue;
        }
        if (!empty($b['eingreifend']) && empty($cfg['eingreifend_ein'])) {
            continue;
        }
        $ein = $basis . '&aktion=' . $name
             . ($name === 'abruf' ? '' : '&fahrzeug=' . (int) $nummer)
             . (isset($b['param']) ? $b['param'] : '');
        $aus = '';
        $titel = vw_t($b['s']);
        if (isset($b['gegen']) && isset($alle[$b['gegen']])) {
            $g = $alle[$b['gegen']];
            if (empty($g['eingreifend']) || !empty($cfg['eingreifend_ein'])) {
                $aus = $basis . '&aktion=' . $b['gegen'] . '&fahrzeug=' . (int) $nummer
                     . (isset($g['param']) ? $g['param'] : '');
                $verbraucht[$b['gegen']] = true;
                $titel = vw_t($b['s']) . ' / ' . vw_t($g['s']);
            }
        }
        /* Die Ja/Nein-Einstellung ist KEIN einzelner Ausgang.
         *
         * Bis 0.9.11 stand der Name 'sitzheizung' fest im Parameter
         * (vw_befehle, 'einstellung'), und die Vorlage enthielt genau einen
         * Ausgang mit dem allgemeinen Titel "Ja/Nein-Einstellung setzen" -
         * er konnte aber nur eines der vier Dinge. vw_schalter() kennt vier;
         * die Oberflaeche zeigte alle vier Adressen zum Abschreiben, die
         * Vorlage nur eine. Jetzt bekommt jeder Schalter seinen eigenen
         * Ausgang mit sprechendem Titel. */
        if ($name === 'einstellung') {
            foreach (vw_schalter() as $vw_sn => $vw_ss) {
                $cmds[] = array(
                    'title'   => 'VW ' . (int) $nummer . ' ' . trim(strip_tags(
                                     html_entity_decode(vw_t($vw_ss), ENT_QUOTES, 'UTF-8'))),
                    'comment' => trim(strip_tags(html_entity_decode(
                                     vw_t($b['s'] . '_H'), ENT_QUOTES, 'UTF-8'))),
                    'on'      => $basis . '&aktion=einstellung&fahrzeug=' . (int) $nummer
                               . '&name=' . $vw_sn . '&wert=<v>',
                    'off'     => '',
                    'analog'  => true,
                );
            }
            continue;
        }
        $cmds[] = array(
            'title'   => 'VW ' . (int) $nummer . ' ' . trim(strip_tags(
                             html_entity_decode($titel, ENT_QUOTES, 'UTF-8'))),
            'comment' => trim(strip_tags(html_entity_decode(
                             vw_t($b['s'] . '_H'), ENT_QUOTES, 'UTF-8'))),
            'on'      => $ein,
            'off'     => $aus,
            'analog'  => !empty($b['analog']),
        );
    }
    return array(
        'volkswagen_fahrzeug' . (int) $nummer . '_befehle.xml',
        vw_xml_virtual_out(array(
            'title'   => 'Volkswagen ' . (int) $nummer . ' ' . vw_t('LOX.ART_BEFEHLE'),
            'address' => 'http://' . $host,
            'comment' => trim(strip_tags(html_entity_decode(
                             vw_t('LOX.VO_COMMENT'), ENT_QUOTES, 'UTF-8'))),
        ), $cmds),
    );
}

/**
 * Eine Vorlage, die nur die Eingaenge fuer den MQTT-Weg anlegt.
 *
 * Die Werte kommen danach vom Gateway, nicht ueber die eingetragene Adresse -
 * deshalb ein Polling von einer Woche und ein Check aus einem Leerzeichen.
 * Bei MQTT ist der TITEL die Adresse; er muss genau dem Thema entsprechen,
 * mit '/' durch '_' ersetzt, so wie das Gateway es bildet.
 *
 * Textthemen bekommen KEINEN Eingang: ein virtueller Eingang in Loxone ist
 * eine Zahl. Wie viele ausgelassen wurden, steht im Kommentar - eine stille
 * Auslassung liest sich wie Vollstaendigkeit.
 */
function vw_vorlage_mqtt($nummer = 1)
{
    $cfg = vw_config();
    $praefix = trim((string) $cfg['mqtt_topic'], '/');
    if ($praefix === '') {
        $praefix = 'volkswagen';
    }
    $cmds = array();
    $text = 0;
    foreach (vw_mqtt_themen() as $thema => $info) {
        $voll = str_replace('fahrzeugN', 'fahrzeug' . (int) $nummer, $thema);
        if (!empty($info['text'])) {
            $text++;
            continue;
        }
        $bedeutung = trim(strip_tags(html_entity_decode(vw_t($info['s']), ENT_QUOTES, 'UTF-8')));
        $einheit = trim(strip_tags(html_entity_decode(
            isset($info['e']) ? $info['e'] : '', ENT_QUOTES, 'UTF-8')));
        $cmds[] = array(
            'title'   => str_replace('/', '_', $praefix . '/' . $voll),
            'comment' => $bedeutung . ($einheit !== '' ? ' [' . $einheit . ']' : ''),
            'check'   => ' ',
            'unit'    => $einheit,
            'analog'  => isset($info['a']) ? $info['a'] : 1,
            'min'     => isset($info['min']) ? $info['min'] : -2147483647,
            'max'     => isset($info['max']) ? $info['max'] : 2147483647,
        );
    }
    return array(
        'volkswagen_fahrzeug' . (int) $nummer . '_mqtt.xml',
        vw_xml_virtual_in_http(array(
            'title'   => 'Volkswagen ' . (int) $nummer . ' MQTT',
            'address' => 'http://localhost',
            'polling' => '604800',
            'comment' => trim(strip_tags(html_entity_decode(
                             sprintf(vw_t('LOX.MQTT_COMMENT'), $text), ENT_QUOTES, 'UTF-8')))
                       . ' (' . date('d.m.Y') . ')',
        ), $cmds),
    );
}

/* ==================================================================
 * Sprache (Pflicht: Deutsch und Englisch)
 *
 * Englisch ist die Rueckfallebene, nicht Deutsch: wer eine dritte Sprache
 * eingestellt hat, versteht eher Englisch. Deshalb muss language_en.ini immer
 * vollstaendig sein.
 *
 * Die Funktion setzt kein vw_paths() voraus, damit derselbe Block in jedes
 * Plugin passt. Der Pfad wird zweistufig gesucht:
 *   installiert: <home>/templates/plugins/<ordner>/lang
 *   Archiv:      <pluginwurzel>/templates/lang
 * ================================================================== */

function vw_sprache()
{
    $sprache = 'de';
    if (class_exists('LBSystem', false) && method_exists('LBSystem', 'lblanguage')) {
        $sprache = LBSystem::lblanguage();
    } elseif (getenv('LBLANG')) {
        $sprache = getenv('LBLANG');
    }
    $sprache = strtolower(substr((string) $sprache, 0, 2));
    return in_array($sprache, array('de', 'en'), true) ? $sprache : 'en';
}

/**
 * Text zu einem Schluessel 'ABSCHNITT.SCHLUESSEL'.
 *
 * Ist der Schluessel unbekannt, wird er selbst zurueckgegeben - so faellt beim
 * Durchsehen sofort auf, was fehlt, statt dass die Seite leer bleibt.
 */
function vw_t($schluessel)
{
    static $texte = null;
    if ($texte === null) {
        $home = getenv('LBHOMEDIR');
        if (!$home || !is_dir($home)) {
            foreach (array(lb_wurzel_ermitteln(), '/home/loxberry/loxberry') as $k) {
                if (is_dir($k)) {
                    $home = $k;
                    break;
                }
            }
        }
        $ordner = basename(dirname(__FILE__));
        $pfad = $home . '/templates/plugins/' . $ordner . '/lang';
        if (!is_dir($pfad)) {
            $pfad = dirname(dirname(dirname(__FILE__))) . '/templates/lang';
        }
        $texte = @parse_ini_file($pfad . '/language_' . vw_sprache() . '.ini', true, INI_SCANNER_RAW);
        if (!is_array($texte)) {
            $texte = array();
        }
        $rueck = @parse_ini_file($pfad . '/language_en.ini', true, INI_SCANNER_RAW);
        if (is_array($rueck)) {
            $texte = array_replace_recursive($rueck, $texte);
        }
        // INI_SCANNER_RAW liefert die Werte samt der Anfuehrungszeichen
        // zurueck, in die sie in der Datei stehen muessen. Die gehoeren nicht
        // in die Ausgabe.
        foreach ($texte as $ab => $paare) {
            if (!is_array($paare)) {
                continue;
            }
            foreach ($paare as $s => $w) {
                $texte[$ab][$s] = trim((string) $w, '"');
            }
        }
    }
    $teile = array_pad(explode('.', $schluessel, 2), 2, '');
    $a = $teile[0];
    $s = $teile[1];
    return isset($texte[$a][$s]) ? $texte[$a][$s] : $schluessel;
}


/**
 * Eine Sicherungsdatei einlesen - und dabei NICHTS durchgehen lassen.
 *
 * Die sieben Punkte aus REGELN_2, und der wichtigste ist der dritte: eine
 * halb gueltige Datei ueberschreibt GAR NICHTS. Wer eine Sicherung
 * zurueckspielt, will entweder den ganzen Stand oder gar keinen - eine zur
 * Haelfte uebernommene Konfiguration ist schlimmer als die alte, und man
 * sieht es ihr nicht an.
 *
 * Unbekannte Schluessel sind eine Beanstandung, kein stiller Verlust: sie
 * stammen aus einer anderen Fassung oder einem anderen Plugin.
 *
 * Rueckgabe: array(Konfiguration|null, Beanstandungen[], uebernommene Werte).
 */
function vw_sicherung_lesen($roh)
{
    $mangel = array();
    $daten = json_decode((string) $roh, true);
    if (!is_array($daten)) {
        return array(null, array(vw_t('EINST.SICH_KEIN_JSON')), 0);
    }
    $neu = vw_vorgaben();
    $bekannt = array_keys($neu);
    $anzahl = 0;
    foreach ($daten as $k => $w) {
        /* Der lesbare Kopf wird UEBERGANGEN, nicht beanstandet. Er stammt aus
         * derselben Bibliothek, die diese Funktion enthaelt - eine Sicherung
         * abzulehnen, die man selbst zwei Zeilen vorher erzeugt hat, ist der
         * Fehler, den WiFi-Scanner NG am 26.08.2026 gemacht hat. */
        if ($k !== '' && $k[0] === '_') {
            continue;
        }
        if (!in_array($k, $bekannt, true)) {
            $mangel[] = sprintf(vw_t('EINST.SICH_FREMD'),
                                 htmlspecialchars((string) $k, ENT_QUOTES, 'UTF-8'));
            continue;
        }
        /* Jeder WERT wird geprueft, nicht nur der Schluessel.
         *
         * Gemessen am 27.08.2026 mit einer Datei, in der alle elf Schluessel
         * bekannt und alle elf Werte Unsinn waren (intervall -5, temp_min 99,
         * ein Objekt im Tokenfeld): sie wurde mit "11 Werte uebernommen"
         * quittiert. Am Endpunkt gab (string) auf das Objekt eine PHP-Warnung
         * und die Zeichenkette "Array" als Vergleichswert. Und ein "0" als
         * Zeichenkette im Steuerungshaken oeffnete das Schreibtor, waehrend
         * die Oberflaeche "gesperrt" anzeigte - bool("0") ist in Python wahr,
         * empty("0") in PHP ebenfalls. */
        list($ok, $rein) = vw_wert_pruefen($k, $w);
        if (!$ok) {
            $mangel[] = sprintf(vw_t('EINST.SICH_WERT'),
                htmlspecialchars((string) $k, ENT_QUOTES, 'UTF-8'),
                htmlspecialchars(is_scalar($w) ? substr((string) $w, 0, 40) : gettype($w),
                                 ENT_QUOTES, 'UTF-8'));
            continue;
        }
        $neu[$k] = $rein;
        $anzahl++;
    }
    if ($anzahl === 0) {
        $mangel[] = vw_t('EINST.SICH_LEER');
    }
    /* FEHLENDE Schluessel sind eine Beanstandung, kein stiller Rueckfall.
     *
     * Bis 0.9.11 war 'vw_vorgaben()' der Ausgangspunkt und nur was in der
     * Datei stand wurde ueberschrieben. Gemessen am 03.09.2026 unter PHP 7.4
     * und 8.4: eine Datei mit dem einen Schluessel {"intervall":300} lief
     * ohne Beanstandung durch, wurde geschrieben, und 26 Einstellungen
     * fielen auf Werk zurueck - darunter das Aktionstoken auf ''. Quittiert
     * wurde das mit "1 Wert uebernommen". Beim naechsten Oeffnen der
     * Oberflaeche entstand ein neues Token, und JEDE im Miniserver
     * eingetragene Adresse war stumm ungueltig.
     *
     * Der Hausstandard sagt: eine halb gueltige Datei aendert gar nichts.
     * Eine Datei, der die Haelfte fehlt, ist halb gueltig. */
    $fehlend = array();
    foreach ($bekannt as $k) {
        if (!array_key_exists($k, $daten)) {
            $fehlend[] = $k;
        }
    }
    if ($fehlend) {
        $mangel[] = sprintf(vw_t('EINST.SICH_FEHLEND'), count($fehlend),
            htmlspecialchars(implode(', ', $fehlend), ENT_QUOTES, 'UTF-8'));
    }
    if (!$mangel && $neu['temp_min'] > $neu['temp_max']) {
        $mangel[] = vw_t('EINST.FEHLER_TEMP_TAUSCH');
    }
    return array($mangel ? null : $neu, $mangel, $anzahl);
}

/**
 * Die Sicherungsdatei, wie sie heruntergeladen wird.
 *
 * Vollstaendig aus den Vorgaben heraus - ein Schluessel, der fehlt, kaeme beim
 * Zurueckspielen aus der Vorgabe, und das ist genau dann falsch, wenn der
 * Anwender ihn bewusst auf den Vorgabewert gesetzt hatte und die Vorgabe sich
 * spaeter aendert.
 *
 * Mit lesbarem Kopf und Datum: wer die Datei in einem Jahr findet, muss
 * erkennen koennen, was sie ist. Die beiden Kopfzeilen beginnen mit einem
 * Unterstrich und werden von vw_sicherung_lesen() uebergangen.
 *
 * DAS AKTIONSTOKEN IST DABEI. Ohne es stuenden nach dem Zurueckspielen alle
 * Felder richtig, und das Plugin kaeme trotzdem nicht an die Anlage - die
 * Datei waere wertlos. Damit traegt sie ein Geheimnis, und der Hinweis am
 * Knopf sagt das. Das Formularmerkmal gehoert NICHT hinein: es wird aus dem
 * Aktionstoken abgeleitet und lebt eine Sitzung lang.
 */
function vw_sicherung_schreiben()
{
    $cfg = vw_config();
    $aus = array(
        '_hinweis' => 'Einstellungen des LoxBerry-Plugins Volkswagen ID. '
                    . 'Enthaelt das Aktionstoken dieser Anlage - wie ein Passwort behandeln.',
        '_stand'   => date('Y-m-d H:i:s'),
        '_fassung' => vw_fassung(),
    );
    foreach (array_keys(vw_vorgaben()) as $k) {
        $aus[$k] = isset($cfg[$k]) ? $cfg[$k] : '';
    }
    return json_encode($aus, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
}

/** Die Fassung aus der plugin.cfg, oder ''. */
function vw_fassung()
{
    static $f = null;
    if ($f !== null) {
        return $f;
    }
    $f = '';
    foreach (array(dirname(dirname(dirname(__FILE__))) . '/plugin.cfg',
                   vw_paths()['home'] . '/config/plugins/' . vw_paths()['plugin'] . '/plugin.cfg') as $p) {
        if ($p !== '' && is_file($p)) {
            /* parse_ini_file scheitert an der plugin.cfg (Kommentare mit
             * Sonderzeichen, unquotierte Werte). Deshalb zeilenweise. */
            foreach (file($p, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: array() as $z) {
                if (preg_match('/^\s*VERSION\s*=\s*([0-9][0-9.]*)/', $z, $m)) {
                    $f = $m[1];
                    return $f;
                }
            }
        }
    }
    return $f;
}
