"""github_ota_helpers.py - "Nach Updates suchen": verbindet sich kurzzeitig
mit einem in wlan.conf hinterlegten WLAN, prueft bei GitHub nach einer
neueren gatehill.nbo-Version und installiert sie automatisch.

Portiert aus source/github_ota_helpers.py (siehe dortiger Docstring fuer die
ausfuehrliche Begruendung der synchronen/blockierenden Bauweise inkl.
feed_wdt/led_tick-Callback-Muster). EIN wichtiger Unterschied zu source/:
source2 (die "gatehill"-Firmware) hat einen EIGENSTAENDIGEN Versionsstand
und eine EIGENSTAENDIGE Release-Pipeline (siehe tools/build_firmware.py's
build_source2_bundle() sowie .github/workflows/build-and-release-gatehill.
yml) mit eigenem Tag-Praefix ("gatehill-vX.Y.Z" statt "vX.Y.Z"). Deshalb
NICHT einfach /repos/{owner}/{repo}/releases/latest abfragen (das waere
das zuletzt veroeffentlichte Release des GESAMTEN Repos - koennte je nach
Reihenfolge auch ein reines Haupt-Firmware-Release ohne gatehill.nbo Asset
sein!), sondern die Release-LISTE nach dem neuesten Release DURCHSUCHEN, das
tatsaechlich ein gatehill.nbo Asset enthaelt (siehe fetch_latest_release()
unten).
"""
import gc
import json
import os
import network
import socket
import time

try:
    import ssl
except Exception:
    import ussl as ssl


HTTPS_PORT = 443
CONNECT_TIMEOUT_S = 10
SOCK_READ_CHUNK = 1024
USER_AGENT = "FPV-GateHill-Pico-OTA/1.0"
MAX_JSON_BODY_BYTES = 65536
RELEASES_PER_PAGE = 5


def _noop_log(_message):
    pass


def _noop_feed_wdt():
    pass


def _noop_led_tick():
    pass


def compare_versions(local_version, remote_version):
    """True, wenn remote_version (z.B. "gatehill-v1.0.3" oder "1.0.3") neuer
    ist als local_version (z.B. "1.0.2"). Robust gegen fehlende/zusaetzliche
    Versionsteile, einen fuehrenden "gatehill-v"/"v" und Suffixe."""

    def _parts(value):
        text = str(value or "").strip()
        if text.startswith("gatehill-"):
            text = text[len("gatehill-"):]
        if text[:1] in ("v", "V"):
            text = text[1:]
        parts = []
        for chunk in text.split("."):
            digits = ""
            for ch in chunk:
                if ch.isdigit():
                    digits += ch
                else:
                    break
            parts.append(int(digits) if digits else 0)
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])

    return _parts(remote_version) > _parts(local_version)


def _split_url(url):
    if url.startswith("https://"):
        rest = url[len("https://"):]
    elif url.startswith("http://"):
        rest = url[len("http://"):]
    else:
        rest = url
    slash_pos = rest.find("/")
    if slash_pos < 0:
        return rest, "/"
    return rest[:slash_pos], rest[slash_pos:]


def _open_tls_socket(host, port=HTTPS_PORT, timeout_s=CONNECT_TIMEOUT_S, log=_noop_log):
    addr_info = socket.getaddrinfo(host, port)[0][-1]
    raw_sock = socket.socket()
    raw_sock.settimeout(timeout_s)
    raw_sock.connect(addr_info)
    try:
        return ssl.wrap_socket(raw_sock, server_hostname=host)
    except TypeError:
        log("[GH-OTA] ssl.wrap_socket ohne SNI (server_hostname nicht unterstuetzt)")
        return ssl.wrap_socket(raw_sock)


def _read_http_head(sock):
    status_line = sock.readline()
    if not status_line:
        raise Exception("Leere HTTP-Antwort")
    status_line = status_line.decode("utf-8", "replace").strip()
    parts = status_line.split(" ", 2)
    status_code = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 0

    headers = {}
    while True:
        line = sock.readline()
        if not line or line in (b"\r\n", b"\n", b""):
            break
        line = line.decode("utf-8", "replace").strip()
        if not line:
            break
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    return status_code, headers


def _read_body_bytes(sock, headers, feed_wdt=_noop_feed_wdt, max_bytes=MAX_JSON_BODY_BYTES):
    """Liest einen kompletten (kleinen) Response-Body in den Speicher - nur
    fuer die GitHub-API-JSON-Antworten gedacht, NICHT fuer Firmware-
    Downloads (siehe _https_download_to_file(), die streamt direkt in eine
    Datei)."""
    if headers.get("transfer-encoding", "").lower() == "chunked":
        body = bytearray()
        while True:
            feed_wdt()
            size_line = sock.readline()
            if not size_line:
                break
            size_line = size_line.strip()
            if not size_line:
                continue
            try:
                chunk_size = int(size_line.split(b";")[0], 16)
            except Exception:
                break
            if chunk_size == 0:
                sock.readline()
                break
            remaining = chunk_size
            while remaining > 0:
                feed_wdt()
                piece = sock.read(min(remaining, SOCK_READ_CHUNK))
                if not piece:
                    break
                body.extend(piece)
                remaining -= len(piece)
                if len(body) > max_bytes:
                    raise Exception("Antwort zu gross")
            sock.readline()
        return bytes(body)

    content_length = headers.get("content-length")
    body = bytearray()
    if content_length is not None:
        remaining = int(content_length)
        while remaining > 0:
            feed_wdt()
            piece = sock.read(min(remaining, SOCK_READ_CHUNK))
            if not piece:
                break
            body.extend(piece)
            remaining -= len(piece)
            if len(body) > max_bytes:
                raise Exception("Antwort zu gross")
        return bytes(body)

    while True:
        feed_wdt()
        piece = sock.read(SOCK_READ_CHUNK)
        if not piece:
            break
        body.extend(piece)
        if len(body) > max_bytes:
            raise Exception("Antwort zu gross")
    return bytes(body)


def _https_request(host, path, headers=None, log=_noop_log, feed_wdt=_noop_feed_wdt, max_body_bytes=MAX_JSON_BODY_BYTES):
    sock = _open_tls_socket(host, log=log)
    try:
        req_lines = [
            "GET {} HTTP/1.1".format(path),
            "Host: {}".format(host),
            "User-Agent: {}".format(USER_AGENT),
            "Connection: close",
        ]
        for key, value in (headers or {}).items():
            req_lines.append("{}: {}".format(key, value))
        req_lines.append("")
        req_lines.append("")
        sock.write("\r\n".join(req_lines).encode("utf-8"))

        status_code, resp_headers = _read_http_head(sock)
        body = _read_body_bytes(sock, resp_headers, feed_wdt=feed_wdt, max_bytes=max_body_bytes)
        return status_code, resp_headers, body
    finally:
        try:
            sock.close()
        except Exception:
            pass


def fetch_latest_release(owner, repo, asset_name, log=_noop_log, feed_wdt=_noop_feed_wdt):
    """Durchsucht die (max. RELEASES_PER_PAGE) neuesten Releases des Repos
    und gibt (tag_name, asset_download_url) des ERSTEN Releases zurueck, das
    tatsaechlich ein Asset mit `asset_name` enthaelt - NICHT einfach
    /releases/latest, da gatehill.nbo einer eigenstaendigen Release-Reihe
    (Tag-Praefix "gatehill-v...") angehoert, die von normalen Haupt-
    Firmware-Releases im selben Repo "ueberdeckt" werden kann (siehe
    Modul-Docstring). Gibt (None, None) zurueck, wenn keines der geprueften
    Releases ein passendes Asset hat."""
    path = "/repos/{}/{}/releases?per_page={}".format(owner, repo, RELEASES_PER_PAGE)
    request_headers = {"Accept": "application/vnd.github+json"}
    try:
        status_code, _resp_headers, body = _https_request(
            "api.github.com", path, headers=request_headers, log=log, feed_wdt=feed_wdt,
        )
    except Exception as e:
        log("[GH-OTA] GitHub-API Verbindung fehlgeschlagen: {}".format(e))
        return None, None

    if status_code != 200:
        log("[GH-OTA] GitHub-API Status {}".format(status_code))
        return None, None

    try:
        releases = json.loads(body)
    except Exception as e:
        log("[GH-OTA] JSON-Fehler: {}".format(e))
        return None, None
    finally:
        del body
        gc.collect()

    if not isinstance(releases, list):
        return None, None

    for release in releases:
        if not isinstance(release, dict):
            continue
        tag_name = release.get("tag_name")
        for asset in release.get("assets", []):
            if asset.get("name") == asset_name:
                return tag_name, asset.get("browser_download_url")

    return None, None


def _https_download_to_file(url, dest_path, max_redirects=5, log=_noop_log, feed_wdt=_noop_feed_wdt, led_tick=_noop_led_tick, progress_cb=None):
    """Laedt `url` (folgt bis zu `max_redirects` 3xx-Redirects, z.B. der
    Weiterleitung von github.com auf objects.githubusercontent.com) direkt
    gestreamt nach `dest_path` - der Body wird NIE komplett im RAM
    gehalten."""
    tmp_path = dest_path + ".tmp"
    current_url = url

    for hop in range(max_redirects + 1):
        host, path = _split_url(current_url)
        log("[GH-OTA] Download-Hop {}: {}{}".format(hop, host, path))
        sock = _open_tls_socket(host, log=log)
        try:
            request_text = (
                "GET {} HTTP/1.1\r\n"
                "Host: {}\r\n"
                "User-Agent: {}\r\n"
                "Accept: */*\r\n"
                "Connection: close\r\n\r\n"
            ).format(path, host, USER_AGENT)
            sock.write(request_text.encode("utf-8"))
            status_code, headers = _read_http_head(sock)

            if status_code in (301, 302, 303, 307, 308):
                location = headers.get("location")
                if not location:
                    raise Exception("Redirect ohne Location-Header")
                current_url = location
                continue

            if status_code != 200:
                raise Exception("HTTP {}".format(status_code))

            content_length_header = headers.get("content-length")
            total_bytes = int(content_length_header) if content_length_header is not None else -1
            received = 0

            try:
                os.remove(tmp_path)
            except Exception:
                pass

            with open(tmp_path, "wb") as out_file:
                chunk_count = 0
                while True:
                    feed_wdt()
                    led_tick()
                    if total_bytes >= 0:
                        to_read = min(SOCK_READ_CHUNK, total_bytes - received)
                        if to_read <= 0:
                            break
                        piece = sock.read(to_read)
                    else:
                        piece = sock.read(SOCK_READ_CHUNK)
                    if not piece:
                        break
                    out_file.write(piece)
                    received += len(piece)
                    chunk_count += 1
                    if progress_cb is not None and total_bytes > 0:
                        try:
                            progress_cb(min(100, (received * 100) // total_bytes))
                        except Exception:
                            pass
                    if chunk_count % 20 == 0:
                        gc.collect()

            if total_bytes >= 0 and received < total_bytes:
                raise Exception("Download unvollstaendig: {}/{} Bytes".format(received, total_bytes))

            try:
                os.remove(dest_path)
            except Exception:
                pass
            os.rename(tmp_path, dest_path)
            log("[GH-OTA] Download abgeschlossen: {} Bytes".format(received))
            return True
        finally:
            try:
                sock.close()
            except Exception:
                pass

    raise Exception("Zu viele Redirects")


def connect_sta_with_retries(ssid, password, attempts=3, timeout_ms=15000, log=_noop_log, feed_wdt=_noop_feed_wdt, led_tick=_noop_led_tick):
    try:
        network.WLAN(network.AP_IF).active(False)
    except Exception:
        pass

    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    time.sleep_ms(200)

    for attempt in range(1, attempts + 1):
        log("[GH-OTA] WLAN-Verbindungsversuch {}/{}: {}".format(attempt, attempts, ssid))
        try:
            sta.disconnect()
        except Exception:
            pass
        try:
            sta.connect(ssid, password)
        except Exception as e:
            log("[GH-OTA] sta.connect() Fehler: {}".format(e))

        start_ms = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), start_ms) < timeout_ms:
            feed_wdt()
            led_tick()
            if sta.isconnected():
                try:
                    log("[GH-OTA] WLAN verbunden: {}".format(sta.ifconfig()[0]))
                except Exception:
                    pass
                return True
            time.sleep_ms(200)

    return False


def disconnect_sta(log=_noop_log):
    try:
        sta = network.WLAN(network.STA_IF)
        sta.disconnect()
        sta.active(False)
    except Exception as e:
        log("[GH-OTA] STA-Trennen fehlgeschlagen: {}".format(e))


def run_update_check(deps):
    """Orchestriert den gesamten Ablauf: WLAN verbinden (3 Versuche) ->
    neuestes gatehill.nbo-Release pruefen -> ggf. Bundle herunterladen +
    anwenden. Siehe source/github_ota_helpers.py's gleichnamige Funktion
    fuer die ausfuehrliche Beschreibung - identischer Ablauf, nur die
    Release-Suche ist anders (siehe fetch_latest_release() oben)."""
    log = deps.get("log", _noop_log)
    feed_wdt = deps.get("feed_wdt", _noop_feed_wdt)
    led_tick = deps.get("led_tick", _noop_led_tick)
    load_wlan_config = deps["load_wlan_config"]
    configure_hotspot = deps["configure_hotspot"]
    ap_ssid = deps["ap_ssid"]
    ap_password = deps["ap_password"]
    firmware_version = deps["firmware_version"]
    apply_firmware_bundle = deps["apply_firmware_bundle"]
    repo_owner = deps["repo_owner"]
    repo_name = deps["repo_name"]
    asset_name = deps["asset_name"]
    staging_path = deps["staging_path"]
    state = deps["state"]

    def _restore_hotspot():
        try:
            disconnect_sta(log=log)
        except Exception:
            pass
        try:
            configure_hotspot(ap_ssid, ap_password, debug_log=log, serial_debug=False)
        except Exception as e:
            log("[GH-OTA] Hotspot-Wiederherstellung fehlgeschlagen: {}".format(e))

    wlan_config = load_wlan_config()
    if not wlan_config.get("ssid"):
        state["phase"] = "no_wlan"
        state["ok"] = False
        state["error"] = "Kein WLAN konfiguriert"
        return

    try:
        state["phase"] = "connecting_wifi"
        state["error"] = ""
        connected = connect_sta_with_retries(
            wlan_config["ssid"], wlan_config["password"],
            attempts=3, timeout_ms=15000,
            log=log, feed_wdt=feed_wdt, led_tick=led_tick,
        )
        if not connected:
            state["phase"] = "wifi_failed"
            state["ok"] = False
            _restore_hotspot()
            return

        state["phase"] = "checking_release"
        tag_name, asset_url = fetch_latest_release(repo_owner, repo_name, asset_name, log=log, feed_wdt=feed_wdt)
        if not tag_name or not asset_url:
            state["phase"] = "check_failed"
            state["ok"] = False
            state["error"] = "Release/Asset nicht gefunden"
            _restore_hotspot()
            return

        state["remote_version"] = tag_name
        if not compare_versions(firmware_version, tag_name):
            state["phase"] = "up_to_date"
            state["ok"] = True
            _restore_hotspot()
            return

        state["phase"] = "downloading"
        state["progress"] = 0

        def _progress(pct):
            state["progress"] = pct

        try:
            os.remove(staging_path)
        except Exception:
            pass
        _https_download_to_file(
            asset_url, staging_path,
            log=log, feed_wdt=feed_wdt, led_tick=led_tick, progress_cb=_progress,
        )

        state["phase"] = "applying"
        extracted_files, _needs_restart = apply_firmware_bundle(staging_path)
        try:
            os.remove(staging_path)
        except Exception:
            pass
        log("[GH-OTA] Update angewendet: {}".format(extracted_files))

        state["phase"] = "success"
        state["ok"] = True
        state["restart_pending"] = True
    except Exception as e:
        log("[GH-OTA] Fehler: {}".format(e))
        state["phase"] = "error"
        state["ok"] = False
        state["error"] = str(e)[:120]
        try:
            os.remove(staging_path)
        except Exception:
            pass
        try:
            os.remove(staging_path + ".tmp")
        except Exception:
            pass
        _restore_hotspot()
