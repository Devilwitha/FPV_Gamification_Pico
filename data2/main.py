"""main.py - FPV_GateHill (source2): eigenstaendige Tor-/Huegel-Firmware.

Schlanke Zusatz-Firmware fuer Picos, die AUSSCHLIESSLICH als King-of-the-Hill-
Huegel oder als Race-Tor (Tor A/Tor B) eingesetzt werden - kein Trick-
Tracking, kein Profile-/Ausweis-/Infection-System, keine Missionen. Nutzt
denselben Boot-/Recovery-/OTA-Unterbau wie die Haupt-Firmware (source/), aber
mit eigenem WLAN-Hotspot-Namen und einer einzigen kombinierten Konfigurations-
seite (index.html) fuer beide Spielmodi.
"""
import machine
import time
import network
import asyncio
import json
import os
import gc
from hotspot_common import configure_hotspot, load_hotspot_config
from ota_helpers import (
    url_decode,
    parse_query,
    safe_base64_file_to_file as _ota_safe_base64_file_to_file,
    apply_firmware_bundle as _ota_apply_firmware_bundle,
    apply_firmware_bundle_from_base64 as _ota_apply_firmware_bundle_from_base64,
)
from koth_mode import KothMode, handle_koth_route
from race_mode import RaceMode, handle_race_route

try:
    import boot_runtime
except Exception:
    boot_runtime = None

# ==================== KONFIGURATION ====================
ENABLE_SERIAL_DEBUG = True
ENABLE_HOTSPOT = True
INDEX_HTML_PATH = "index.html"
FIRMWARE_VERSION_FILE = "firmware_version.txt"

_HOTSPOT_CONFIG = load_hotspot_config()
AP_SSID = _HOTSPOT_CONFIG["ssid"]
AP_PASSWORD = _HOTSPOT_CONFIG["password"]
# =======================================================

OTA_STAGING_PATH = "ota_staging.tmp"
# Eigene, schlanke Whitelist fuer source2 (nur Tor/Huegel-Dateien - keine
# Trick-/Infection-/Profil-Dateien der Haupt-Firmware).
OTA_ALLOWED_TARGETS = (
    "boot.py", "recovery.py", "hotspot_common.py", "hotspot.conf", "boot_runtime.py",
    "ota_helpers.py", "koth_mode.py", "race_mode.py",
    "main.py", "index.html",
    "firmware_version.txt",
)
OTA_BUNDLE_TARGET = "gatehill.nbo"
OTA_BUNDLE_MAGIC = b"FPVBNDL1"

ota_total_chunks = 0
ota_received_chunks = 0
ota_target_file = "main.py"
ota_update_active = False

boot_health_marked = False
system_ready = False


def debug_log(message):
    if ENABLE_SERIAL_DEBUG:
        print(f"[DEBUG] [{time.ticks_ms() // 1000}s] {message}")


def _boot_feed_watchdog():
    if boot_runtime is None:
        return
    try:
        boot_runtime.feed_wdt()
    except Exception:
        pass


def _boot_mark_healthy_once():
    global boot_health_marked
    if boot_health_marked or boot_runtime is None:
        return
    try:
        boot_runtime.clear_main_fail_count()
        boot_health_marked = True
        debug_log("[BOOT] Main-Start als gesund markiert")
    except Exception:
        pass


def safe_base64_file_to_file(input_file, output_file):
    return _ota_safe_base64_file_to_file(input_file, output_file, log=debug_log, feed_wdt=_boot_feed_watchdog)


def apply_firmware_bundle(bundle_path):
    return _ota_apply_firmware_bundle(bundle_path, OTA_ALLOWED_TARGETS, OTA_BUNDLE_MAGIC, log=debug_log, feed_wdt=_boot_feed_watchdog)


def apply_firmware_bundle_from_base64(base64_path):
    return _ota_apply_firmware_bundle_from_base64(base64_path, OTA_ALLOWED_TARGETS, OTA_BUNDLE_MAGIC, log=debug_log, feed_wdt=_boot_feed_watchdog)


def start_access_point():
    configure_hotspot(
        AP_SSID,
        AP_PASSWORD,
        debug_log=lambda m: debug_log(f"[AP] {m}"),
        serial_debug=ENABLE_SERIAL_DEBUG,
    )


def _read_firmware_version():
    try:
        with open(FIRMWARE_VERSION_FILE, "r") as f:
            return f.read().strip()
    except Exception:
        return "?"


async def send_html_file(writer, file_path):
    gc.collect()
    file_size = os.stat(file_path)[6]
    writer.write(b'HTTP/1.1 200 OK\r\n')
    writer.write(b'Content-Type: text/html; charset=utf-8\r\n')
    writer.write(b'Content-Length: ' + str(file_size).encode() + b'\r\n')
    writer.write(b'Connection: close\r\n\r\n')
    with open(file_path, 'rb') as f:
        chunk_count = 0
        while True:
            chunk = f.read(512)
            if not chunk:
                break
            writer.write(chunk)
            chunk_count += 1
            if chunk_count % 4 == 0:
                await writer.drain()
    await writer.drain()
    gc.collect()


def _json_response(writer, status_line, data):
    body = json.dumps(data).encode('utf-8')
    writer.write(b'HTTP/1.1 ' + status_line.encode() + b'\r\n')
    writer.write(b'Content-Type: application/json\r\n')
    writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
    writer.write(b'Content-Length: ' + str(len(body)).encode() + b'\r\n')
    writer.write(b'Connection: close\r\n\r\n')
    writer.write(body)


# Eager statt lazy: source2 ist klein genug, dass beide Spielmodi + BLE
# sofort beim Start initialisiert werden koennen, ohne die ~85KB-Compile-
# Grenze der Haupt-Firmware zu riskieren.
koth_manager = KothMode("", debug_log)
race_manager = RaceMode("", debug_log)


async def handle_client(reader, writer):
    global ota_total_chunks, ota_received_chunks, ota_update_active, ota_target_file
    try:
        request_line = await reader.readline()
        if not request_line:
            return

        request = request_line.decode('utf-8')
        parts = request.split(' ')
        request_method = parts[0] if len(parts) >= 1 else 'GET'
        request_target = parts[1] if len(parts) >= 2 else '/'

        if ENABLE_SERIAL_DEBUG:
            print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [HTTP] {request_method} {request_target}")

        if '?' in request_target:
            request_path, query_string = request_target.split('?', 1)
        else:
            request_path, query_string = request_target, ''

        query_params = parse_query(query_string)
        content_length = 0
        while True:
            line = await reader.readline()
            if line == b'\r\n' or line == b'\n' or not line:
                break
            try:
                line_text = line.decode('utf-8').strip()
            except Exception:
                line_text = ''
            if line_text.lower().startswith('content-length:'):
                try:
                    content_length = int(line_text.split(':', 1)[1].strip())
                except Exception:
                    content_length = 0

        body_text = ""
        body_params = {}
        if request_method == 'POST' and content_length > 0:
            try:
                body_buffer = bytearray()
                bytes_remaining = content_length
                chunk_size = 2048
                while bytes_remaining > 0:
                    to_read = min(chunk_size, bytes_remaining)
                    chunk = await reader.read(to_read)
                    if not chunk:
                        break
                    body_buffer.extend(chunk)
                    bytes_remaining -= len(chunk)
                if body_buffer:
                    try:
                        body_text = body_buffer.decode('utf-8')
                        if request_path != '/upload-chunk':
                            body_params = parse_query(body_text)
                    except Exception:
                        body_text = ""
            except Exception as e:
                debug_log(f"[HTTP] Fehler beim Lesen des POST Body: {e}")

        if request_path == '/' or request_path == '/index.html':
            await send_html_file(writer, INDEX_HTML_PATH)

        elif request_path.startswith('/koth-'):
            await handle_koth_route(writer, request_path, request_method, query_params, body_params, koth_manager)

        elif request_path.startswith('/race-'):
            await handle_race_route(writer, request_path, request_method, query_params, body_params, race_manager)

        elif request_path == '/system-info':
            try:
                mem_free = gc.mem_free()
            except Exception:
                mem_free = -1
            try:
                ip_addr = network.WLAN(network.AP_IF).ifconfig()[0]
            except Exception:
                ip_addr = ""
            _json_response(writer, '200 OK', {
                "mem_free": mem_free,
                "uptime_s": time.ticks_ms() // 1000,
                "ssid": AP_SSID,
                "ip": ip_addr,
                "firmware_version": _read_firmware_version(),
                "ota_active": ota_update_active,
            })

        elif request_path == '/upload-chunk' and request_method == 'POST':
            chunk_index_str = '-1'
            total_str = '0'
            target_str = 'main.py'
            if body_text:
                idx_pos = body_text.find('index=')
                if idx_pos >= 0:
                    idx_start = idx_pos + 6
                    idx_end = body_text.find('&', idx_start)
                    if idx_end < 0:
                        idx_end = len(body_text)
                    chunk_index_str = url_decode(body_text[idx_start:idx_end])

                total_pos = body_text.find('total=')
                if total_pos >= 0:
                    total_start = total_pos + 6
                    total_end = body_text.find('&', total_start)
                    if total_end < 0:
                        total_end = len(body_text)
                    total_str = url_decode(body_text[total_start:total_end])

                target_pos = body_text.find('target=')
                if target_pos >= 0:
                    target_start = target_pos + 7
                    target_end = body_text.find('&', target_start)
                    if target_end < 0:
                        target_end = len(body_text)
                    target_str = url_decode(body_text[target_start:target_end])

            chunk_data = ''
            if body_text:
                marker = '&data='
                pos = body_text.find(marker)
                if pos >= 0:
                    chunk_data = url_decode(body_text[pos + len(marker):])
                elif body_text.startswith('data='):
                    chunk_data = url_decode(body_text[5:])

            try:
                chunk_index = int(chunk_index_str)
                total = int(total_str)
                target_valid = True

                if chunk_index == 0:
                    if target_str not in OTA_ALLOWED_TARGETS and target_str != OTA_BUNDLE_TARGET:
                        target_valid = False
                    else:
                        ota_total_chunks = total
                        ota_received_chunks = 0
                        ota_update_active = True
                        ota_target_file = target_str
                        try:
                            os.remove('update.pbp')
                        except Exception:
                            pass
                        debug_log(f"[OTA] Chunk-Transfer gestartet: {total} Chunks erwartet, Ziel={target_str}")

                if not target_valid:
                    _json_response(writer, '400 Bad Request', {"ok": False, "error": f"Ungueltiges Ziel: {target_str}"})
                else:
                    if chunk_data:
                        with open('update.pbp', 'a') as f:
                            f.write(chunk_data)
                        ota_received_chunks += 1

                    _json_response(writer, '200 OK', {"ok": True, "message": f"Chunk {chunk_index + 1}/{total} gespeichert"})
                gc.collect()

            except Exception as e:
                debug_log(f"[OTA CHUNK] Fehler: {e}")
                ota_update_active = False
                _json_response(writer, '400 Bad Request', {"ok": False, "error": str(e)})

        elif request_path == '/finalize-upload':
            try:
                if ota_received_chunks != ota_total_chunks:
                    raise Exception(f"Unvollstaendiger Upload: {ota_received_chunks}/{ota_total_chunks}")

                is_bundle = (ota_target_file == OTA_BUNDLE_TARGET)
                target = ota_target_file if (is_bundle or ota_target_file in OTA_ALLOWED_TARGETS) else "main.py"

                if is_bundle:
                    extracted_files, needs_restart = apply_firmware_bundle_from_base64('update.pbp')
                    try:
                        os.remove('update.pbp')
                    except Exception:
                        pass
                    message = f"Firmware-Bundle angewendet: {len(extracted_files)} Datei(en) ersetzt ({', '.join(extracted_files)})"
                    if needs_restart:
                        message += " Starte Neustart..."
                else:
                    decode_ok = safe_base64_file_to_file('update.pbp', OTA_STAGING_PATH)
                    if decode_ok is not True:
                        raise Exception(f"Base64 Dekodierung fehlgeschlagen: {decode_ok}")
                    try:
                        os.remove('update.pbp')
                    except Exception:
                        pass
                    try:
                        staged_size = os.stat(OTA_STAGING_PATH)[6]
                    except Exception:
                        staged_size = 0
                    try:
                        os.remove(target)
                    except Exception:
                        pass
                    os.rename(OTA_STAGING_PATH, target)
                    needs_restart = (target == "main.py")
                    message = f"Update erfolgreich gespeichert: {target} ({staged_size} bytes)!"
                    if needs_restart:
                        message += " Starte Neustart..."

                _json_response(writer, '200 OK', {"ok": True, "message": message, "restart": needs_restart})

                if boot_runtime is not None:
                    try:
                        boot_runtime.request_main_retry_once()
                    except Exception:
                        pass

                globals()['ota_total_chunks'] = 0
                globals()['ota_received_chunks'] = 0
                globals()['ota_update_active'] = False
                try:
                    os.remove('update.pbp')
                except Exception:
                    pass

                try:
                    await writer.drain()
                except Exception:
                    pass

                if needs_restart:
                    await asyncio.sleep_ms(2000)
                    machine.reset()

            except Exception as e:
                debug_log(f"[OTA FINALIZE] Fehler: {str(e)[:100]}")
                _json_response(writer, '500 Internal Server Error', {"ok": False, "error": str(e)[:100]})
                ota_total_chunks = 0
                ota_received_chunks = 0
                ota_update_active = False
                try:
                    os.remove('update.pbp')
                except Exception:
                    pass
                try:
                    os.remove(OTA_STAGING_PATH)
                except Exception:
                    pass

        elif request_path == '/restart-pico':
            _json_response(writer, '200 OK', {"ok": True, "message": "Pico startet neu..."})
            if boot_runtime is not None:
                try:
                    boot_runtime.request_main_retry_once()
                except Exception:
                    pass
            try:
                await writer.drain()
            except Exception:
                pass
            await asyncio.sleep_ms(1000)
            machine.reset()

        else:
            body = b'Not Found'
            writer.write(b'HTTP/1.1 404 Not Found\r\n')
            writer.write(b'Content-Type: text/plain\r\n')
            writer.write(b'Content-Length: ' + str(len(body)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(body)

        await writer.drain()
    except OSError as e:
        if len(e.args) > 0 and e.args[0] == 104:
            pass
        else:
            debug_log(f"[WEB ERROR] {e}")
    except Exception as e:
        debug_log(f"[WEB ERROR] {e}")
    finally:
        try:
            writer.close()
        except Exception:
            pass
        try:
            wait_closed = getattr(writer, 'wait_closed', None)
            if wait_closed is not None:
                await wait_closed()
        except Exception:
            pass
        await asyncio.sleep_ms(5)


async def main_async():
    global system_ready
    if ENABLE_HOTSPOT:
        boot_present = False
        try:
            os.stat("boot.py")
            boot_present = True
        except Exception:
            boot_present = False

        if boot_present:
            debug_log("[BOOT] boot.py gefunden, Hotspot-Start in main uebersprungen.")
        else:
            debug_log("[BOOT] boot.py fehlt, starte Hotspot aus main.")
            start_access_point()

    await asyncio.start_server(handle_client, "0.0.0.0", 80)
    asyncio.create_task(koth_manager.run())
    asyncio.create_task(race_manager.run())
    _boot_mark_healthy_once()
    system_ready = True
    debug_log("FPV_GateHill (source2) laeuft. WLAN verbinden und http://192.168.4.1 aufrufen.")
    while True:
        _boot_feed_watchdog()
        await asyncio.sleep_ms(1000)


def run():
    debug_log("main.py (source2) wurde gestartet.")
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        debug_log("System manuell gestoppt.")


run()
