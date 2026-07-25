"""
ota_helpers.py - gemeinsame OTA-/Kodier-Hilfsfunktionen fuer main.py und recovery.py.

Ausgelagert aus main.py (spaeter Session) aus zwei Gruenden:
  1. main.py ist mit >2000 Zeilen / ~95KB Quelltext gross genug, dass
     MicroPython beim `import main` in boot.py gelegentlich mit
     "memory allocation failed" abstuerzt - NICHT weil insgesamt zu wenig
     RAM frei ist (gc.mem_free() zeigt kurz vor dem Crash oft noch >180KB
     frei), sondern weil MicroPythons Compiler beim Parsen einer so grossen
     Datei viele unterschiedlich grosse temporaere Objekte alloziert/
     reallokiert und der (nicht kompaktierende!) GC dabei den Heap in viele
     kleine Luecken fragmentiert - am Ende scheitert eine einzelne, an sich
     kleine Allokation (z.B. 3033 Bytes), obwohl in Summe genug frei waere.
     Kleinere Dateien = kleinerer Spitzenspeicherbedarf beim Kompilieren
     EINER einzelnen Datei, daher hilft Auslagern von Code in eigene Module.
  2. Diese Funktionen waren VORHER in main.py UND recovery.py dupliziert
     (Kommentar "manuell synchron halten") - das ist jetzt nicht mehr noetig.

Alle Funktionen, die vorher main.py's/recovery.py's debug_log() direkt
aufgerufen haben, nehmen hier stattdessen einen optionalen `log`-Callback
entgegen (Default: no-op). Das vermeidet einen zirkulaeren Import
(main.py importiert dieses Modul - nicht umgekehrt).

WICHTIG: ota_helpers.py muss auf dem Pico IMMER vorhanden sein, wenn
main.py oder recovery.py aktuell ist (beide importieren es). Es ist Teil
von build_firmware.py's FILES_TO_BUNDLE und von OTA_ALLOWED_TARGETS in
main.py/recovery.py, damit es sowohl per Firmware-Bundle als auch
einzeln aktualisiert werden kann.
"""
import os
import struct


def _noop_log(_message):
    pass


def _noop_feed_wdt():
    pass


# Bundles (firmware.nbo/lang.pak) werden ausschliesslich von build_firmware.py
# auf dem PC zusammengestellt - die enthaltenen Dateien muessen daher NICHT
# mehr einzeln gegen die feste OTA_ALLOWED_TARGETS-Liste geprueft werden (das
# bremste z.B. neue Mission-Dateien mit beliebigen Namen aus, obwohl ein
# Bundle als Ganzes bereits eine vertrauenswuerdige Update-Einheit ist). Es
# bleibt aber eine strukturelle Sicherheitspruefung, damit ein praeparierter
# Bundle-Dateiname kein Pfad-Traversal (z.B. "../boot.py") oder eine versteckte
# Datei erzeugen kann.
def _is_safe_bundle_entry_filename(filename):
    if not filename:
        return False
    if "/" in filename or "\\" in filename or ".." in filename:
        return False
    if filename.startswith("."):
        return False
    return True


def url_decode(value):
    value = value.replace('+', ' ')
    out = ""
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == '%' and i + 2 < len(value):
            hex_part = value[i + 1:i + 3]
            try:
                out += chr(int(hex_part, 16))
                i += 3
                continue
            except Exception:
                pass
        out += ch
        i += 1
    return out


def parse_query(query_string):
    params = {}
    if not query_string:
        return params
    pairs = query_string.split('&')
    for pair in pairs:
        if not pair:
            continue
        if '=' in pair:
            key, value = pair.split('=', 1)
        else:
            key, value = pair, ''
        params[url_decode(key)] = url_decode(value)
    return params


def base64_decode(s):
    import base64
    try:
        return base64.b2a_base64(base64.a2b_base64(s + b'==')).decode('utf-8').strip()
    except Exception:
        return None


def safe_base64_decode_to_file(b64_string, output_file, log=_noop_log):
    try:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        with open(output_file, 'wb') as f:
            chunk_size = 512
            for chunk_start in range(0, len(b64_string), chunk_size):
                chunk = b64_string[chunk_start:chunk_start + chunk_size]
                chunk_result = bytearray()
                padding = (4 - len(chunk) % 4) % 4
                chunk_padded = chunk + "=" * padding

                for i in range(0, len(chunk_padded), 4):
                    group = chunk_padded[i:i + 4]
                    if len(group) < 4:
                        continue

                    nums = []
                    for c in group:
                        idx = alphabet.find(c)
                        nums.append(idx if idx >= 0 else 0)

                    b1 = (nums[0] << 2) | (nums[1] >> 4)
                    b2 = ((nums[1] & 0xF) << 4) | (nums[2] >> 2)
                    b3 = ((nums[2] & 0x3) << 6) | nums[3]

                    chunk_result.append(b1)
                    if group[2] != '=':
                        chunk_result.append(b2)
                    if group[3] != '=':
                        chunk_result.append(b3)

                f.write(chunk_result)
        return True
    except Exception as e:
        log(f"[BASE64-FILE] Fehler: {e}")
        return False


def safe_base64_file_to_file(input_file, output_file, log=_noop_log, feed_wdt=_noop_feed_wdt):
    try:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        carry = ""

        with open(input_file, 'r') as fin:
            with open(output_file, 'wb') as fout:
                while True:
                    # Watchdog jeden Chunk fuettern: diese Funktion ist rein
                    # synchron (kein await), blockiert also die komplette
                    # asyncio-Eventloop (inkl. der Task, die sonst den
                    # Watchdog fuettert) - bei grossen Bundles (>8s Gesamt-
                    # dauer) wuerde sonst der Hardware-Watchdog (siehe
                    # boot.py, Timeout 8000ms) mitten im Request feuern und
                    # die TCP-Verbindung abrupt beenden ("Connection reset").
                    feed_wdt()
                    chunk = fin.read(512)
                    if not chunk:
                        break

                    data = carry + chunk
                    usable_len = (len(data) // 4) * 4
                    to_decode = data[:usable_len]
                    carry = data[usable_len:]

                    out_bytes = bytearray()
                    for i in range(0, len(to_decode), 4):
                        group = to_decode[i:i + 4]
                        if len(group) < 4:
                            continue

                        nums = []
                        for c in group:
                            idx = alphabet.find(c)
                            nums.append(idx if idx >= 0 else 0)

                        b1 = (nums[0] << 2) | (nums[1] >> 4)
                        b2 = ((nums[1] & 0xF) << 4) | (nums[2] >> 2)
                        b3 = ((nums[2] & 0x3) << 6) | nums[3]

                        out_bytes.append(b1)
                        if group[2] != '=':
                            out_bytes.append(b2)
                        if group[3] != '=':
                            out_bytes.append(b3)

                    if out_bytes:
                        fout.write(out_bytes)

                if carry:
                    padding = (4 - len(carry) % 4) % 4
                    group = carry + ("=" * padding)
                    out_bytes = bytearray()
                    for i in range(0, len(group), 4):
                        g = group[i:i + 4]
                        if len(g) < 4:
                            continue

                        nums = []
                        for c in g:
                            idx = alphabet.find(c)
                            nums.append(idx if idx >= 0 else 0)

                        b1 = (nums[0] << 2) | (nums[1] >> 4)
                        b2 = ((nums[1] & 0xF) << 4) | (nums[2] >> 2)
                        b3 = ((nums[2] & 0x3) << 6) | nums[3]

                        out_bytes.append(b1)
                        if g[2] != '=':
                            out_bytes.append(b2)
                        if g[3] != '=':
                            out_bytes.append(b3)

                    if out_bytes:
                        fout.write(out_bytes)
        return True
    except Exception as e:
        log(f"[BASE64-FILE-STREAM] Fehler: {e}")
        return str(e) or "unbekannter Fehler"


def read_exact(f, n):
    """Liest exakt n Bytes aus einer binaer geoeffneten Datei (oder weniger
    bei EOF). Noetig, weil f.read(n) theoretisch weniger als n Bytes liefern
    kann, auch wenn noch nicht das Dateiende erreicht ist."""
    data = bytearray()
    while len(data) < n:
        chunk = f.read(n - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _apply_firmware_bundle_from_stream(f, allowed_targets, bundle_magic, log=_noop_log, feed_wdt=_noop_feed_wdt):
    """Kern-Logik von apply_firmware_bundle()/apply_firmware_bundle_from_base64():
    liest ein Bundle aus einem beliebigen Objekt mit .read(n) (echte Datei
    ODER Base64StreamReader) und ersetzt jede enthaltene Datei einzeln auf
    dem Pico-Dateisystem ohne Backup-Dateien, um Flash-Spitzen zu vermeiden.
    Jeder Dateiname im Bundle wird nur noch strukturell geprueft (siehe
    _is_safe_bundle_entry_filename) statt gegen die feste `allowed_targets`-
    Liste - ein Bundle ist bereits eine vertrauenswuerdige, von
    build_firmware.py auf dem PC zusammengestellte Update-Einheit, daher
    keine Einzeldatei-Whitelist-Pruefung mehr noetig (`allowed_targets` bleibt
    nur aus Kompatibilitaetsgruenden im Funktionssignatur erhalten).

    `feed_wdt` wird bei jeder Datei UND bei jedem Schreib-Chunk aufgerufen -
    diese Funktion ist komplett synchron (kein await) und kann bei grossen
    Bundles (viele/grosse Dateien) laenger als der Hardware-Watchdog-Timeout
    (8000ms, siehe boot.py) brauchen; ohne diese Callbacks wuerde der
    Watchdog mitten im Entpacken feuern und die Verbindung abrupt kappen."""
    extracted_files = []
    magic = read_exact(f, len(bundle_magic))
    if magic != bundle_magic:
        raise Exception("Ungueltiges Firmware-Bundle (Magic-Header falsch)")

    count_bytes = read_exact(f, 4)
    if len(count_bytes) < 4:
        raise Exception("Bundle beschaedigt (Dateianzahl fehlt)")
    (file_count,) = struct.unpack('>I', count_bytes)

    for _ in range(file_count):
        feed_wdt()
        name_len_bytes = read_exact(f, 4)
        if len(name_len_bytes) < 4:
            raise Exception("Bundle beschaedigt (Namenslaenge fehlt)")
        (name_len,) = struct.unpack('>I', name_len_bytes)

        name_bytes = read_exact(f, name_len)
        if len(name_bytes) < name_len:
            raise Exception("Bundle beschaedigt (Dateiname unvollstaendig)")
        filename = name_bytes.decode('utf-8')

        content_len_bytes = read_exact(f, 4)
        if len(content_len_bytes) < 4:
            raise Exception(f"Bundle beschaedigt (Inhaltslaenge fehlt: {filename})")
        (content_len,) = struct.unpack('>I', content_len_bytes)

        if not _is_safe_bundle_entry_filename(filename):
            raise Exception(f"Datei im Bundle nicht erlaubt: {filename}")

        tmp_name = filename + ".bndl_tmp"
        remaining = content_len
        with open(tmp_name, 'wb') as out:
            while remaining > 0:
                feed_wdt()
                chunk = f.read(min(512, remaining))
                if not chunk:
                    raise Exception(f"Bundle beschaedigt (Inhalt unvollstaendig: {filename})")
                out.write(chunk)
                remaining -= len(chunk)

        try:
            os.remove(filename)
        except Exception:
            pass
        os.rename(tmp_name, filename)

        extracted_files.append(filename)
        log(f"[OTA BUNDLE] Datei ersetzt: {filename} ({content_len} bytes)")

    needs_restart = "main.py" in extracted_files
    return extracted_files, needs_restart


def apply_firmware_bundle(bundle_path, allowed_targets, bundle_magic, log=_noop_log, feed_wdt=_noop_feed_wdt):
    """Entpackt ein bereits binaer vorliegendes Firmware-Bundle
    (z.B. firmware.nbo) aus `bundle_path`. Siehe _apply_firmware_bundle_from_stream()."""
    with open(bundle_path, 'rb') as f:
        return _apply_firmware_bundle_from_stream(f, allowed_targets, bundle_magic, log=log, feed_wdt=feed_wdt)


class Base64FileReader:
    """Liest eine Base64-Text-Datei (z.B. 'update.pbp') und liefert ueber
    .read(n) direkt dekodierte Binaerdaten - OHNE den kompletten dekodierten
    Bundle-Inhalt jemals komplett auf dem Dateisystem zu materialisieren.

    Grund: bei einem OTA-Firmware-Bundle (mehrere Dateien in einem Blob)
    hat das vorherige Vorgehen (erst komplett nach OTA_STAGING_PATH
    dekodieren, DANACH entpacken) einen zusaetzlichen, voll dekodierten
    Bundle-Inhalt (mehrere hundert KB) waehrend des GESAMTEN Entpack-
    vorgangs auf dem Flash gehalten - zusaetzlich zu den noch nicht
    ersetzten Original-Dateien. Bei wachsender Bundle-Groesse (Sprachpakete,
    neue Features) fuehrte das zu OSError 28 (ENOSPC, kein Speicherplatz
    mehr frei). Mit dieser Klasse wird direkt aus der Base64-Datei
    dekodiert, waehrend apply_firmware_bundle_from_base64() das Bundle
    parst - es existiert nie mehr als ein kleiner Puffer decodierter
    Bytes gleichzeitig im Speicher, und KEINE zusaetzliche Datei auf dem
    Flash fuer den dekodierten Bundle-Inhalt."""

    _ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="

    def __init__(self, text_file, feed_wdt=_noop_feed_wdt):
        self._f = text_file
        self._feed_wdt = feed_wdt
        self._text_carry = ""
        self._byte_buffer = bytearray()
        self._eof = False

    def _decode_group(self, group, out):
        nums = []
        for c in group:
            idx = self._ALPHABET.find(c)
            nums.append(idx if idx >= 0 else 0)
        b1 = (nums[0] << 2) | (nums[1] >> 4)
        b2 = ((nums[1] & 0xF) << 4) | (nums[2] >> 2)
        b3 = ((nums[2] & 0x3) << 6) | nums[3]
        out.append(b1)
        if group[2] != '=':
            out.append(b2)
        if group[3] != '=':
            out.append(b3)

    def _fill(self, min_bytes):
        while len(self._byte_buffer) < min_bytes and not self._eof:
            self._feed_wdt()
            chunk = self._f.read(512)
            if not chunk:
                self._eof = True
                if self._text_carry:
                    padding = (4 - len(self._text_carry) % 4) % 4
                    group = self._text_carry + ("=" * padding)
                    for i in range(0, len(group), 4):
                        g = group[i:i + 4]
                        if len(g) < 4:
                            continue
                        self._decode_group(g, self._byte_buffer)
                    self._text_carry = ""
                break

            data = self._text_carry + chunk
            usable_len = (len(data) // 4) * 4
            to_decode = data[:usable_len]
            self._text_carry = data[usable_len:]
            for i in range(0, len(to_decode), 4):
                self._decode_group(to_decode[i:i + 4], self._byte_buffer)

    def read(self, n):
        self._fill(n)
        result = bytes(self._byte_buffer[:n])
        # MicroPython's bytearray unterstuetzt keine Slice-Loeschung
        # (del arr[:n] -> "'bytearray' object doesn't support item
        # deletion"), daher stattdessen per Slice neu zuweisen statt zu
        # loeschen (funktioniert auf CPython UND MicroPython gleich).
        self._byte_buffer = self._byte_buffer[len(result):]
        return result


def apply_firmware_bundle_from_base64(base64_path, allowed_targets, bundle_magic, log=_noop_log, feed_wdt=_noop_feed_wdt):
    """Wie apply_firmware_bundle(), liest das Bundle aber direkt aus einer
    NOCH base64-kodierten Datei (z.B. 'update.pbp') und dekodiert es
    stromweise waehrend des Entpackens - ohne jemals eine komplett
    dekodierte Kopie des Bundles auf dem Flash zu erzeugen (siehe
    Base64FileReader). Spart bei grossen Bundles hunderte KB Flash-
    Spitzenbedarf gegenueber dem Umweg ueber safe_base64_file_to_file()
    + apply_firmware_bundle()."""
    with open(base64_path, 'r') as text_f:
        reader = Base64FileReader(text_f, feed_wdt=feed_wdt)
        return _apply_firmware_bundle_from_stream(reader, allowed_targets, bundle_magic, log=log, feed_wdt=feed_wdt)
