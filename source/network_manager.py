"""network_manager.py - Boot-WLAN-Check (Firmware-Suche + Webshop-Mod-Sync)
und temporaerer WLAN-Download einzelner Mods.

Ausgelagert aus main.py (wie github_ota_helpers.py/gmr.py), damit main.py's
Kompiliergroesse beim riskanten `import main` in boot.py nicht waechst -
wird lazy importiert.

WICHTIG (gleiche Einschraenkung wie github_ota_helpers.py, siehe dortiger
Docstring): kein zuverlaessiges non-blocking TLS/Socket-Handling auf diesem
Geraet - alle Netzwerkfunktionen hier sind bewusst SYNCHRON/blockierend.
Die eigentliche STA-Verbindung + das garantierte Zurueckschalten in den
Access-Point-Betrieb (in JEDEM Pfad, Erfolg wie Fehler) wird NICHT neu
gebaut, sondern wiederverwendet: github_ota_helpers.connect_sta_with_retries()
/ disconnect_sta() - exakt dieselben, bereits fuer den manuellen GitHub-
Update-Button genutzten Bausteine.

Firmware-Update-Check laeuft bewusst ueber GitHub (github_ota_helpers.
fetch_latest_release()/compare_versions()), NICHT ueber den Webshop-Server -
der Webshop liefert ausschliesslich die Mod-Liste/-Dateien.
"""

import gc
import json
import os
import socket
import time

STORE_CACHE_FILE = "store_cache.json"
STORE_HOST = "46.4.78.34"
STORE_PORT = 5000
BOOT_STA_TIMEOUT_MS = 9000  # 8-10s Budget fuer den Boot-Verbindungsversuch
HTTP_READ_CHUNK = 1024
MAX_HTTP_BODY_BYTES = 40960

# Read-only Status fuer pico_web_api.py (analog main.py's github_ota_state).
network_state = {
    "last_check_ms": 0,
    "wlan_connected": False,
    "fw_update_available": False,
    "fw_current_version": "",
    "fw_latest_version": "",
    "store_sync_ok": False,
    "store_error": "",
}


def _noop_log(_message):
    pass


def _noop_feed_wdt():
    pass


def _http_get_json(host, port, path, timeout_s=8):
    """Minimaler, blockierender Klartext-HTTP-GET (kein TLS noetig - der
    Webshop laeuft auf Port 5000 ohne HTTPS). Gibt das geparste JSON-Objekt
    zurueck oder wirft eine Exception."""
    addr_info = socket.getaddrinfo(host, port)[0][-1]
    sock = socket.socket()
    sock.settimeout(timeout_s)
    try:
        sock.connect(addr_info)
        request = (
            "GET {} HTTP/1.1\r\n"
            "Host: {}\r\n"
            "User-Agent: FPV-Gamification-Pico-Store/1.0\r\n"
            "Connection: close\r\n\r\n"
        ).format(path, host)
        sock.write(request.encode("utf-8"))

        status_line = sock.readline()
        if not status_line:
            raise Exception("Leere HTTP-Antwort")
        parts = status_line.decode("utf-8", "replace").strip().split(" ", 2)
        status_code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0

        headers = {}
        while True:
            line = sock.readline()
            if not line or line in (b"\r\n", b"\n", b""):
                break
            line = line.decode("utf-8", "replace").strip()
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()

        body = bytearray()
        content_length = headers.get("content-length")
        if content_length is not None:
            remaining = int(content_length)
            while remaining > 0:
                piece = sock.read(min(remaining, HTTP_READ_CHUNK))
                if not piece:
                    break
                body.extend(piece)
                remaining -= len(piece)
                if len(body) > MAX_HTTP_BODY_BYTES:
                    raise Exception("Antwort zu gross")
        else:
            while True:
                piece = sock.read(HTTP_READ_CHUNK)
                if not piece:
                    break
                body.extend(piece)
                if len(body) > MAX_HTTP_BODY_BYTES:
                    raise Exception("Antwort zu gross")

        if status_code != 200:
            raise Exception("HTTP {}".format(status_code))
        return json.loads(bytes(body).decode("utf-8"))
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _download_file(host, port, path, dest_path, timeout_s=10):
    addr_info = socket.getaddrinfo(host, port)[0][-1]
    sock = socket.socket()
    sock.settimeout(timeout_s)
    try:
        sock.connect(addr_info)
        request = (
            "GET {} HTTP/1.1\r\n"
            "Host: {}\r\n"
            "User-Agent: FPV-Gamification-Pico-Store/1.0\r\n"
            "Connection: close\r\n\r\n"
        ).format(path, host)
        sock.write(request.encode("utf-8"))

        status_line = sock.readline()
        parts = status_line.decode("utf-8", "replace").strip().split(" ", 2)
        status_code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0

        headers = {}
        while True:
            line = sock.readline()
            if not line or line in (b"\r\n", b"\n", b""):
                break
            line = line.decode("utf-8", "replace").strip()
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip().lower()] = value.strip()

        if status_code != 200:
            raise Exception("HTTP {}".format(status_code))

        content_length = headers.get("content-length")
        remaining = int(content_length) if content_length is not None else None
        with open(dest_path, "wb") as out_file:
            while remaining is None or remaining > 0:
                to_read = HTTP_READ_CHUNK if remaining is None else min(HTTP_READ_CHUNK, remaining)
                piece = sock.read(to_read)
                if not piece:
                    break
                out_file.write(piece)
                if remaining is not None:
                    remaining -= len(piece)
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _save_store_cache(plugins):
    payload = json.dumps({"plugins": plugins, "synced_ms": time.ticks_ms()})
    temp_path = STORE_CACHE_FILE + ".tmp"
    try:
        with open(temp_path, "w") as f:
            f.write(payload)
        try:
            os.remove(STORE_CACHE_FILE)
        except Exception:
            pass
        os.rename(temp_path, STORE_CACHE_FILE)
    except Exception:
        pass


def load_store_cache():
    try:
        with open(STORE_CACHE_FILE, "r") as f:
            return json.loads(f.read())
    except Exception:
        return {"plugins": [], "synced_ms": 0}


def _ensure_mod_dir(name):
    try:
        os.mkdir("mods")
    except Exception:
        pass
    try:
        os.mkdir("mods/" + name)
    except Exception:
        pass


def boot_network_check(deps):
    """Wird einmalig beim Start von main.py aufgerufen, BEVOR der Webserver
    startet - blockiert daher bewusst kurz (siehe Docstring). Ablauf:
    STA-Verbindungsversuch (8-10s) -> bei Erfolg GitHub-Firmware-Check +
    Webshop-Mod-Listen-Sync -> in JEDEM Fall zurueck in den Access-Point-
    Betrieb, bevor main.py den Server startet."""
    import github_ota_helpers

    log = deps.get("log", _noop_log)
    feed_wdt = deps.get("feed_wdt", _noop_feed_wdt)
    load_wlan_config = deps["load_wlan_config"]
    configure_hotspot = deps["configure_hotspot"]
    ap_ssid = deps["ap_ssid"]
    ap_password = deps["ap_password"]

    network_state["last_check_ms"] = time.ticks_ms()
    network_state["fw_current_version"] = deps.get("firmware_version", "")

    wlan_config = load_wlan_config()
    if not wlan_config.get("ssid"):
        log("[NET] Kein WLAN konfiguriert - bleibe im Access-Point-Betrieb")
        return

    connected = github_ota_helpers.connect_sta_with_retries(
        wlan_config["ssid"],
        wlan_config.get("password", ""),
        attempts=1,
        timeout_ms=BOOT_STA_TIMEOUT_MS,
        log=log,
        feed_wdt=feed_wdt,
    )
    network_state["wlan_connected"] = connected

    try:
        if connected:
            _run_boot_checks(deps, github_ota_helpers, log, feed_wdt)
    finally:
        try:
            github_ota_helpers.disconnect_sta(log=log)
        except Exception:
            pass
        try:
            configure_hotspot(ap_ssid, ap_password, debug_log=log, serial_debug=False)
        except Exception as e:
            log("[NET] Hotspot-Wiederherstellung fehlgeschlagen: {}".format(e))


def _run_boot_checks(deps, github_ota_helpers, log, feed_wdt):
    firmware_version = deps.get("firmware_version", "0.0.0")
    repo_owner = deps["repo_owner"]
    repo_name = deps["repo_name"]
    asset_name = deps["asset_name"]

    try:
        tag_name, _asset_url = github_ota_helpers.fetch_latest_release(
            repo_owner, repo_name, asset_name, log=log, feed_wdt=feed_wdt,
        )
        if tag_name:
            network_state["fw_latest_version"] = tag_name
            network_state["fw_update_available"] = github_ota_helpers.compare_versions(firmware_version, tag_name)
        else:
            network_state["fw_update_available"] = False
    except Exception as e:
        log("[NET] Firmware-Check fehlgeschlagen: {}".format(e))

    try:
        response = _http_get_json(STORE_HOST, STORE_PORT, "/api/plugins")
        plugins = response.get("plugins", []) if isinstance(response, dict) else []
        _save_store_cache(plugins)
        network_state["store_sync_ok"] = True
        network_state["store_error"] = ""
    except Exception as e:
        network_state["store_sync_ok"] = False
        network_state["store_error"] = str(e)[:120]
        log("[NET] Webshop-Sync fehlgeschlagen: {}".format(e))

    gc.collect()


def download_plugin_via_wifi(plugin_name, deps):
    """Verbindet sich kurzzeitig mit dem WLAN (gleiches Muster wie
    boot_network_check()), laedt die Dateien eines einzelnen Mods vom
    Webshop herunter und aktiviert es danach ueber plugin_manager. Kein
    ZIP/Tar-Handling auf dem Pico noetig - der Webshop liefert pro Mod eine
    einfache Dateiliste (siehe webshop/app.py's /api/plugins), jede Datei
    wird einzeln ueber Flasks Standard-static-Serving geholt."""
    import github_ota_helpers
    import plugin_manager

    log = deps.get("log", _noop_log)
    feed_wdt = deps.get("feed_wdt", _noop_feed_wdt)
    load_wlan_config = deps["load_wlan_config"]
    configure_hotspot = deps["configure_hotspot"]
    ap_ssid = deps["ap_ssid"]
    ap_password = deps["ap_password"]

    result = {"ok": False, "error": ""}

    wlan_config = load_wlan_config()
    if not wlan_config.get("ssid"):
        result["error"] = "Kein WLAN konfiguriert"
        return result

    connected = github_ota_helpers.connect_sta_with_retries(
        wlan_config["ssid"],
        wlan_config.get("password", ""),
        attempts=1,
        timeout_ms=BOOT_STA_TIMEOUT_MS,
        log=log,
        feed_wdt=feed_wdt,
    )

    try:
        if not connected:
            result["error"] = "WLAN-Verbindung fehlgeschlagen"
            return result

        try:
            response = _http_get_json(STORE_HOST, STORE_PORT, "/api/plugins")
            plugins = response.get("plugins", []) if isinstance(response, dict) else []
        except Exception as e:
            result["error"] = "Mod-Liste nicht erreichbar: {}".format(e)
            return result

        plugin_info = None
        for entry in plugins:
            if entry.get("name") == plugin_name:
                plugin_info = entry
                break
        if plugin_info is None:
            result["error"] = "Mod '{}' nicht im Webshop gefunden".format(plugin_name)
            return result

        files = plugin_info.get("files") or []
        if not files:
            result["error"] = "Mod '{}' hat keine Dateien".format(plugin_name)
            return result

        _ensure_mod_dir(plugin_name)
        for filename in files:
            feed_wdt()
            path = "/static/plugins_store/{}/{}".format(plugin_name, filename)
            dest = "mods/{}/{}".format(plugin_name, filename)
            _download_file(STORE_HOST, STORE_PORT, path, dest)

        plugin_manager.load_all_plugins()
        result["ok"] = True
        return result
    except Exception as e:
        result["error"] = str(e)[:160]
        return result
    finally:
        try:
            github_ota_helpers.disconnect_sta(log=log)
        except Exception:
            pass
        try:
            configure_hotspot(ap_ssid, ap_password, debug_log=log, serial_debug=False)
        except Exception as e:
            log("[NET] Hotspot-Wiederherstellung fehlgeschlagen: {}".format(e))
