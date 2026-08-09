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

function vw_paths()
{
    static $p = null;
    if ($p !== null) {
        return $p;
    }
    $home = getenv('LBHOMEDIR');
    if (!$home || !is_dir($home)) {
        foreach (array('/opt/loxberry', '/home/loxberry/loxberry') as $k) {
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
        );
    }
    return $p;
}

/** Voreinstellungen. Muessen zu VORGABEN in bin/vw.py passen. */
function vw_vorgaben()
{
    return array(
        'intervall'         => 300,
        'takt_wartung'      => 12,
        'mqtt_ein'          => 0,
        'mqtt_topic'        => 'volkswagen',
        'steuerung_ein'     => 0,
        'temp_min'          => 16,
        'temp_max'          => 29,
        'verlauf_tage'      => 8,
        'zugriff_erzwingen' => 0,
        'aktionstoken'      => '',
        'wartezeit'         => 8,
    );
}

function vw_json_lesen($pfad)
{
    if (!is_file($pfad)) {
        return array();
    }
    $d = json_decode((string) @file_get_contents($pfad), true);
    return is_array($d) ? $d : array();
}

function vw_config()
{
    $p = vw_paths();
    // Selbstheilung: fehlende oder leere Konfiguration aus der Sicherung holen.
    $roh = is_file($p['config']) ? trim((string) @file_get_contents($p['config'])) : '';
    if (($roh === '' || $roh === '{}') && is_file($p['sicherung'])) {
        @mkdir($p['configdir'], 0775, true);
        @copy($p['sicherung'], $p['config']);
    }
    $cfg = vw_json_lesen($p['config']);
    return array_merge(vw_vorgaben(), $cfg);
}

function vw_config_speichern($cfg)
{
    $p = vw_paths();
    if (!is_dir($p['configdir'])) {
        @mkdir($p['configdir'], 0775, true);
    }
    $json = json_encode($cfg, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    // json_encode liefert bei ungueltigem UTF-8 false, und file_put_contents
    // schriebe dann eine Datei mit NULL Bytes - und meldete das als Erfolg.
    if ($json === false || @file_put_contents($p['config'], $json) === false) {
        return false;
    }
    @copy($p['config'], $p['sicherung']);
    return true;
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

/** Sorgt dafuer, dass ein Token vorhanden ist, und gibt es zurueck. */
function vw_token()
{
    $cfg = vw_config();
    if (trim((string) $cfg['aktionstoken']) === '') {
        $cfg['aktionstoken'] = vw_token_erzeugen();
        vw_config_speichern($cfg);
    }
    return (string) $cfg['aktionstoken'];
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
    $cfg = vw_config();
    if ($wartezeit === null) {
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

/** Messpunkte eines Tages: Array von array(ts, fuellstand, reichweite). */
function vw_verlauf_lesen($nummer, $tag = '')
{
    if ($tag === '') {
        $tag = date('Ymd');
    }
    $f = vw_paths()['datadir'] . '/verlauf/fahrzeug' . (int) $nummer . '_' . $tag . '.csv';
    $out = array();
    if (is_file($f)) {
        foreach (file($f, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES) ?: array() as $zeile) {
            $c = explode(';', $zeile);
            if (count($c) >= 2) {
                $out[] = array((int) $c[0], (float) $c[1], isset($c[2]) && $c[2] !== '' ? (float) $c[2] : 0);
            }
        }
    }
    return $out;
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
    $leer = array('gefunden' => 0, 'autostart' => 0, 'udpport' => 0,
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
        'udpport'    => (int) $hol('Udpinport', 'udpinport'),
        'broker'     => (string) $hol('Brokerhost', 'brokerhost'),
        'brokerport' => (string) $hol('Brokerport', 'brokerport'),
        'websocket'  => (string) $hol('Websocketport', 'websocketport'),
    );
}

/** Alle Themen, die der Dienst veroeffentlicht, mit ihrer Bedeutung. */
function vw_mqtt_themen()
{
    return array(
        'ok'                          => 'VW_MQTT.OK',
        'fahrzeuge'                   => 'VW_MQTT.FAHRZEUGE',
        'fahrzeugN/soc'               => 'VW_MQTT.SOC',
        'fahrzeugN/tank_prozent'      => 'VW_MQTT.TANK',
        'fahrzeugN/reichweite_km'     => 'VW_MQTT.REICHWEITE',
        'fahrzeugN/kilometerstand'    => 'VW_MQTT.KM',
        'fahrzeugN/verriegelt'        => 'VW_MQTT.VERRIEGELT',
        'fahrzeugN/tueren_offen'      => 'VW_MQTT.TUEREN',
        'fahrzeugN/fenster_offen'     => 'VW_MQTT.FENSTER',
        'fahrzeugN/licht_an'          => 'VW_MQTT.LICHT',
        'fahrzeugN/handbremse'        => 'VW_MQTT.HANDBREMSE',
        'fahrzeugN/zustand'           => 'VW_MQTT.ZUSTAND',
        'fahrzeugN/erreichbar'        => 'VW_MQTT.ERREICHBAR',
        'fahrzeugN/klima_an'          => 'VW_MQTT.KLIMA',
        'fahrzeugN/zieltemperatur'    => 'VW_MQTT.ZIELTEMP',
        'fahrzeugN/aussentemperatur'  => 'VW_MQTT.AUSSEN',
        'fahrzeugN/scheibenheizung'   => 'VW_MQTT.SCHEIBE',
        'fahrzeugN/laedt'             => 'VW_MQTT.LAEDT',
        'fahrzeugN/ladeleistung_kw'   => 'VW_MQTT.LADEKW',
        'fahrzeugN/ladetempo_kmh'     => 'VW_MQTT.TEMPO',
        'fahrzeugN/ladegrenze'        => 'VW_MQTT.LADEGRENZE',
        'fahrzeugN/ladestrom_a'       => 'VW_MQTT.LADESTROM',
        'fahrzeugN/kabel_verbunden'   => 'VW_MQTT.KABEL',
        'fahrzeugN/stecker_verriegelt' => 'VW_MQTT.STECKER',
        'fahrzeugN/laden_fertig_um'   => 'VW_MQTT.FERTIG',
        'fahrzeugN/breite'            => 'VW_MQTT.BREITE',
        'fahrzeugN/laenge'            => 'VW_MQTT.LAENGE',
        'fahrzeugN/inspektion_tage'   => 'VW_MQTT.INSP_TAGE',
        'fahrzeugN/inspektion_km'     => 'VW_MQTT.INSP_KM',
        'fahrzeugN/oelservice_tage'   => 'VW_MQTT.OEL_TAGE',
        'fahrzeugN/oelservice_km'     => 'VW_MQTT.OEL_KM',
    );
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

function vw_xml_virtual_in_http($kopf, $cmds)
{
    $crlf = "\r\n";
    $o = '<?xml version="1.0" encoding="utf-8"?>' . $crlf;
    $o .= '<VirtualInHttp ';
    $o .= 'Title="' . vw_x($kopf['title']) . '" ';
    $o .= 'Comment="' . vw_x(isset($kopf['comment']) ? $kopf['comment'] : '') . '" ';
    $o .= 'Address="' . vw_x(isset($kopf['address']) ? $kopf['address'] : '') . '" ';
    $o .= 'PollingTime="' . vw_x(isset($kopf['polling']) ? $kopf['polling'] : '60') . '"';
    $o .= '>' . $crlf;
    foreach ($cmds as $c) {
        $o .= "\t" . '<VirtualInHttpCmd ';
        $o .= 'Title="' . vw_x($c['title']) . '" ';
        $o .= 'Comment="' . vw_x(isset($c['comment']) ? $c['comment'] : '') . '" ';
        $o .= 'Check="' . vw_x(isset($c['check']) ? $c['check'] : ' ') . '" ';
        $o .= 'Signed="true" ';
        $o .= 'Analog="true" ';
        $o .= 'SourceValLow="0" ';
        $o .= 'DestValLow="0" ';
        $o .= 'SourceValHigh="100" ';
        $o .= 'DestValHigh="100" ';
        $o .= 'DefVal="0" ';
        $o .= 'MinVal="-2147483647" ';
        $o .= 'MaxVal="2147483647"';
        $o .= '/>' . $crlf;
    }
    $o .= '</VirtualInHttp>' . $crlf;
    return $o;
}

/**
 * Die Werte des Status-Endpunkts mit Einheit und Bedeutung.
 *
 * Reihenfolge und Namen sind zugleich die Reihenfolge der Befehlserkennungen
 * in der Loxone-Vorlage. Wer hier etwas einfuegt, aendert die Vorlage mit.
 */
function vw_status_felder()
{
    return array(
        'SOC'       => array('%',   'VW_FELD.SOC'),
        'TANK'      => array('%',   'VW_FELD.TANK'),
        'REICHW'    => array('km',  'VW_FELD.REICHW'),
        'KM'        => array('km',  'VW_FELD.KM'),
        'VERR'      => array('',    'VW_FELD.VERR'),
        'TUEREN'    => array('',    'VW_FELD.TUEREN'),
        'FENSTER'   => array('',    'VW_FELD.FENSTER'),
        'LICHT'     => array('',    'VW_FELD.LICHT'),
        'HANDBR'    => array('',    'VW_FELD.HANDBR'),
        'KLIMA'     => array('',    'VW_FELD.KLIMA'),
        'ZIELTEMP'  => array('&deg;C', 'VW_FELD.ZIELTEMP'),
        'AUSSEN'    => array('&deg;C', 'VW_FELD.AUSSEN'),
        'SCHEIBE'   => array('',    'VW_FELD.SCHEIBE'),
        'ZUSTAND'   => array('',    'VW_FELD.ZUSTAND'),
        'ERREICH'   => array('',    'VW_FELD.ERREICH'),
        'ALTER'     => array('s',   'VW_FELD.ALTER'),
        'OK'        => array('',    'VW_FELD.OK'),
    );
}

/** Die Werte des Lade-Endpunkts. */
function vw_laden_felder()
{
    return array(
        'SOC'       => array('%',    'VW_LFELD.SOC'),
        'LAEDT'     => array('',     'VW_LFELD.LAEDT'),
        'LADEKW'    => array('kW',   'VW_LFELD.LADEKW'),
        'TEMPO'     => array('km/h', 'VW_LFELD.TEMPO'),
        'LADEGR'    => array('%',    'VW_LFELD.LADEGR'),
        'LADESTROM' => array('A',    'VW_LFELD.LADESTROM'),
        'KABEL'     => array('',     'VW_LFELD.KABEL'),
        'STECKER'   => array('',     'VW_LFELD.STECKER'),
        'REICHWBAT' => array('km',   'VW_LFELD.REICHWBAT'),
        'FERTIGMIN' => array('min',  'VW_LFELD.FERTIGMIN'),
        'OK'        => array('',     'VW_LFELD.OK'),
    );
}

/** Die Werte des Wartungs-Endpunkts. */
function vw_wartung_felder()
{
    return array(
        'INSPTAGE'  => array('d',   'VW_WFELD.INSPTAGE'),
        'INSPKM'    => array('km',  'VW_WFELD.INSPKM'),
        'OELTAGE'   => array('d',   'VW_WFELD.OELTAGE'),
        'OELKM'     => array('km',  'VW_WFELD.OELKM'),
        'KM'        => array('km',  'VW_WFELD.KM'),
        'OK'        => array('',    'VW_WFELD.OK'),
    );
}

/** Vorlage fuer den Import in Loxone Config. Rueckgabe: array(name, inhalt) */
function vw_vorlage($nummer = 1)
{
    $p = vw_paths();
    $host = isset($_SERVER['HTTP_HOST']) && $_SERVER['HTTP_HOST'] !== ''
        ? preg_replace('/[^A-Za-z0-9\.\-:]/', '', (string) $_SERVER['HTTP_HOST'])
        : (gethostname() ?: 'loxberry');
    $token = vw_token();
    $cmds = array();
    foreach (vw_status_felder() as $feld => $info) {
        // Der Text laeuft gleich durch vw_x() und wuerde dort ein zweites Mal
        // maskiert. Deshalb erst Auszeichnung entfernen und Entitaeten
        // aufloesen - sonst stuende in Loxone Config wortwoertlich
        // 'l&auml;dt' statt 'laedt'.
        $bedeutung = trim(strip_tags(html_entity_decode(vw_t($info[1]), ENT_QUOTES, 'UTF-8')));
        $einheit = trim(strip_tags(html_entity_decode($info[0], ENT_QUOTES, 'UTF-8')));
        $cmds[] = array(
            'title'   => 'VW_' . $nummer . '_' . $feld,
            'comment' => $bedeutung . ($einheit !== '' ? ' [' . $einheit . ']' : ''),
            'check'   => '\i' . $feld . '=\i\v',
        );
    }
    $adresse = 'http://' . $host . '/plugins/' . $p['plugin']
             . '/index.php?token=' . $token . '&aktion=status&fahrzeug=' . (int) $nummer;
    return array(
        'volkswagen_fahrzeug' . (int) $nummer . '.xml',
        vw_xml_virtual_in_http(array(
            'title'   => 'Volkswagen ' . (int) $nummer,
            'address' => $adresse,
            'polling' => '300',
            'comment' => 'Erzeugt vom LoxBerry-Plugin Volkswagen ID (' . date('d.m.Y') . ')',
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
            foreach (array('/opt/loxberry', '/home/loxberry/loxberry') as $k) {
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
