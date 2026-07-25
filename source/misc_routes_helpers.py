import gc
import json
import network
import os
import time


async def handle_misc_routes(
    writer,
    request_path,
    request_method,
    query_params,
    body_text,
    body_params,
    trick_tuning_profile,
    developer_mode_enabled,
    language_code,
    deps,
):
    send_html_file = deps["send_html_file"]
    admin_profiles_html_path = deps["admin_profiles_html_path"]
    admin_system_html_path = deps["admin_system_html_path"]
    ap_ssid = deps["ap_ssid"]
    enable_hotspot = deps["enable_hotspot"]
    detector = deps["detector"]
    highscore_data = deps["highscore_data"]
    pending_highscore = deps["pending_highscore"]
    default_pilot_name = deps["default_pilot_name"]
    firmware_version = deps["firmware_version"]
    ota_update_active = deps["ota_update_active"]
    ota_received_chunks = deps["ota_received_chunks"]
    ota_total_chunks = deps["ota_total_chunks"]
    list_profile_files = deps["list_profile_files"]
    get_copil_payload = deps["get_copil_payload"]
    save_copil_names = deps["save_copil_names"]
    save_custom_profile = deps["save_custom_profile"]
    get_profile_data = deps["get_profile_data"]
    delete_custom_profile = deps["delete_custom_profile"]
    activate_trick_profile = deps["activate_trick_profile"]
    debug_log = deps["debug_log"]
    debug_console_only = deps["debug_console_only"]
    save_system_settings = deps["save_system_settings"]
    get_language_code = deps["get_language_code"]
    set_language_code = deps["set_language_code"]
    is_allowed_language = deps["is_allowed_language"]
    list_language_codes = deps["list_language_codes"]
    send_highscore_name_response = deps["send_highscore_name_response"]
    send_confirm_highscore_response = deps["send_confirm_highscore_response"]
    send_reset_highscore_response = deps["send_reset_highscore_response"]
    enable_serial_debug = deps["enable_serial_debug"]
    write_text_file = deps["write_text_file"]
    session_export_file_path = deps["session_export_file_path"]
    build_session_txt_content = deps["build_session_txt_content"]
    send_file_as_download = deps["send_file_as_download"]
    build_debug_export_file = deps["build_debug_export_file"]
    debug_export_file_path = deps["debug_export_file_path"]
    init_debug_log_file = deps["init_debug_log_file"]
    simulate_trick = deps["simulate_trick"]
    perform_emergency_delete_main = deps["perform_emergency_delete_main"]
    perform_emergency_delete_boot = deps["perform_emergency_delete_boot"]
    infection_status = deps.get("infection_status")

    if request_path == '/admin-profiles':
        await send_html_file(writer, admin_profiles_html_path)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/admin-system':
        await send_html_file(writer, admin_system_html_path)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/system-info':
        try:
            mem_free = gc.mem_free()
        except Exception:
            mem_free = -1
        try:
            mem_alloc = gc.mem_alloc()
        except Exception:
            mem_alloc = -1

        try:
            vfs = os.statvfs('.')
            fs_block_size = int(vfs[0])
            fs_total = fs_block_size * int(vfs[2])
            fs_free = fs_block_size * int(vfs[3])
            fs_used = fs_total - fs_free
        except Exception:
            fs_total = -1
            fs_free = -1
            fs_used = -1

        ip_addr = ""
        if enable_hotspot:
            try:
                ip_addr = network.WLAN(network.AP_IF).ifconfig()[0]
            except Exception:
                ip_addr = ""

        info_data = {
            "mem_free": mem_free,
            "mem_alloc": mem_alloc,
            "fs_total": fs_total,
            "fs_free": fs_free,
            "fs_used": fs_used,
            "uptime_s": time.ticks_ms() // 1000,
            "ssid": ap_ssid,
            "ip": ip_addr,
            "hotspot_enabled": enable_hotspot,
            "trick_tuning_profile": trick_tuning_profile,
            "score": detector.score,
            "highscore": highscore_data["score"],
            "ota_active": ota_update_active,
            "ota_received_chunks": ota_received_chunks,
            "ota_total_chunks": ota_total_chunks,
            "developer_mode": developer_mode_enabled,
            "language": language_code,
            "firmware_version": firmware_version,
        }
        response_data = json.dumps(info_data).encode('utf-8')
        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
        writer.write(b'Pragma: no-cache\r\n')
        writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response_data)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/hotspot-config':
        try:
            with open('hotspot.conf', 'r') as config_file:
                config = json.loads(config_file.read())
            ssid = str(config.get('ssid', ap_ssid))
            password = str(config.get('password', ''))
        except Exception:
            ssid = ap_ssid
            password = ''
        response_data = json.dumps({
            "ok": True,
            "ssid": ssid,
            "password": password,
        }).encode('utf-8')
        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store\r\n')
        writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response_data)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/set-hotspot-config' and request_method == 'POST':
        ssid = body_params.get('ssid', '').strip()
        password = body_params.get('password', '')
        error = ''
        if not ssid or len(ssid) > 32:
            error = 'SSID muss 1 bis 32 Zeichen lang sein'
        elif len(password) < 8 or len(password) > 63:
            error = 'Passwort muss 8 bis 63 Zeichen lang sein'
        if error:
            response_data = json.dumps({"ok": False, "error": error}).encode('utf-8')
            writer.write(b'HTTP/1.1 400 Bad Request\r\n')
        else:
            try:
                temp_path = 'hotspot.conf.tmp'
                with open(temp_path, 'w') as config_file:
                    config_file.write(json.dumps({"ssid": ssid, "password": password}))
                try:
                    os.remove('hotspot.conf')
                except Exception:
                    pass
                os.rename(temp_path, 'hotspot.conf')
                response_data = json.dumps({
                    "ok": True,
                    "message": "Hotspot gespeichert. Neustart erforderlich.",
                }).encode('utf-8')
                writer.write(b'HTTP/1.1 200 OK\r\n')
            except Exception as error_value:
                response_data = json.dumps({"ok": False, "error": str(error_value)}).encode('utf-8')
                writer.write(b'HTTP/1.1 500 Internal Server Error\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store\r\n')
        writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response_data)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/language-packs':
        codes = list_language_codes()
        response_data = json.dumps({
            "ok": True,
            "current": language_code,
            "languages": codes,
        }).encode('utf-8')
        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
        writer.write(b'Pragma: no-cache\r\n')
        writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response_data)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/i18n-data':
        selected = query_params.get('lang', language_code)
        fallback = 'en'
        if not selected or not is_allowed_language(selected):
            selected = fallback
        if not selected or not is_allowed_language(selected):
            selected = fallback

        strings = {}
        base_strings = {}
        try:
            with open(fallback + '.pak', 'r') as f:
                parsed_base = json.loads(f.read())
            if isinstance(parsed_base, dict):
                base_strings = parsed_base
        except Exception:
            base_strings = {}

        strings = dict(base_strings)
        try:
            with open(selected + '.pak', 'r') as f:
                parsed = json.loads(f.read())
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    strings[k] = v
        except Exception:
            pass

        response_data = json.dumps({
            "ok": True,
            "lang": selected,
            "strings": strings,
        }).encode('utf-8')
        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
        writer.write(b'Pragma: no-cache\r\n')
        writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response_data)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/profiles-list':
        profiles_list = list_profile_files()
        profiles_data = []
        for prof in profiles_list:
            profiles_data.append({
                "name": prof,
                "active": prof == trick_tuning_profile
            })

        response_data = json.dumps({"ok": True, "profiles": profiles_data}).encode('utf-8')
        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
        writer.write(b'Pragma: no-cache\r\n')
        writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response_data)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/copil-info':
        response_data = json.dumps({"ok": True, "copil": get_copil_payload()}).encode('utf-8')
        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
        writer.write(b'Pragma: no-cache\r\n')
        writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response_data)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/set-copil' and request_method == 'POST':
        copter_name = body_params.get('copter_name', '').strip()
        pilot_name = body_params.get('pilot_name', '').strip()
        ok, err = save_copil_names(copter_name, pilot_name)
        if ok:
            response = json.dumps({"ok": True, "copil": get_copil_payload()}).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
        else:
            response = json.dumps({"ok": False, "error": err}).encode('utf-8')
            writer.write(b'HTTP/1.1 500 Internal Server Error\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/create-profile' and request_method == 'POST':
        profile_name = body_params.get('name', '').strip()
        profile_data_str = body_params.get('data', '').strip()

        if not profile_name or not profile_data_str:
            response = json.dumps({"ok": False, "error": "Name oder Daten fehlen"}).encode('utf-8')
            writer.write(b'HTTP/1.1 400 Bad Request\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response)
        else:
            try:
                profile_data = json.loads(profile_data_str)
                success, error = save_custom_profile(profile_name, profile_data)
                if success:
                    response = json.dumps({"ok": True, "message": "Profil %s erstellt" % profile_name}).encode('utf-8')
                    writer.write(b'HTTP/1.1 200 OK\r\n')
                else:
                    response = json.dumps({"ok": False, "error": error}).encode('utf-8')
                    writer.write(b'HTTP/1.1 400 Bad Request\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
            except Exception as e:
                response = json.dumps({"ok": False, "error": str(e)}).encode('utf-8')
                writer.write(b'HTTP/1.1 500 Internal Server Error\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/download-profile':
        profile_name = query_params.get('name', '').strip()
        if not profile_name:
            response = json.dumps({"ok": False, "error": "Profil-Name fehlt"}).encode('utf-8')
            writer.write(b'HTTP/1.1 400 Bad Request\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response)
        else:
            profile_data = get_profile_data(profile_name)
            if profile_data is None:
                response = json.dumps({"ok": False, "error": "Profil nicht gefunden: %s" % profile_name}).encode('utf-8')
                writer.write(b'HTTP/1.1 404 Not Found\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
            else:
                response_data = json.dumps(profile_data).encode('utf-8')
                writer.write(b'HTTP/1.1 200 OK\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Disposition: attachment; filename="' + profile_name.encode('utf-8') + b'.pro"\r\n')
                writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response_data)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/delete-profile':
        profile_name = query_params.get('name', '').strip()
        if not profile_name:
            response = json.dumps({"ok": False, "error": "Profil-Name fehlt"}).encode('utf-8')
        else:
            success, error = delete_custom_profile(profile_name)
            if success:
                response = json.dumps({"ok": True, "message": "Profil %s geloescht" % profile_name}).encode('utf-8')
            else:
                response = json.dumps({"ok": False, "error": error}).encode('utf-8')

        response = response.encode('utf-8') if isinstance(response, str) else response
        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/apply-profile':
        profile_name = query_params.get('name', '').strip()

        if not profile_name:
            response = json.dumps({"ok": False, "error": "Profil-Name fehlt"}).encode('utf-8')
        else:
            saved_ok, save_error, profile_name = activate_trick_profile(profile_name)
            trick_tuning_profile = profile_name

            if saved_ok:
                response = json.dumps({"ok": True, "profile": profile_name}).encode('utf-8')
                debug_log("[PROFILE] Angewendet: " + profile_name)
            else:
                response = json.dumps({"ok": False, "error": save_error}).encode('utf-8')

        response = response.encode('utf-8') if isinstance(response, str) else response
        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
        writer.write(b'Pragma: no-cache\r\n')
        writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/data':
        data = {
            "score": detector.score,
            "history": detector.trick_history,
            "highscore": highscore_data["score"],
            "highscore_timestamp": highscore_data["timestamp"],
            "highscore_player": highscore_data.get("player", default_pilot_name),
            "trick_tuning_profile": trick_tuning_profile,
            "pending_highscore": pending_highscore["active"],
            "pending_highscore_score": pending_highscore["score"],
            "firmware_version": firmware_version,
        }
        if infection_status is not None:
            try:
                data["infection"] = infection_status()
            except Exception:
                data["infection"] = {"enabled": False, "running": False}
        response_data = json.dumps(data).encode('utf-8')
        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
        writer.write(b'Pragma: no-cache\r\n')
        writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response_data)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/set-highscore-name':
        await send_highscore_name_response(writer, query_params, body_params)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/set-trick-profile':
        saved_ok, save_error, profile_name = activate_trick_profile(query_params.get('profile', 'aggressive'))
        trick_tuning_profile = profile_name

        if saved_ok:
            debug_console_only("[TRICK PROFILE] Profil gespeichert: " + trick_tuning_profile)

        payload = json.dumps({
            "ok": saved_ok,
            "error": "" if saved_ok else ("Speichern fehlgeschlagen: " + str(save_error)),
            "trick_tuning_profile": trick_tuning_profile
        }).encode('utf-8')

        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
        writer.write(b'Pragma: no-cache\r\n')
        writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(payload)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/set-developer-mode':
        developer_mode_enabled = query_params.get('enabled', '0') == '1'
        saved_ok, save_error = save_system_settings(enabled=developer_mode_enabled)

        if saved_ok:
            debug_console_only("[SYSTEM] Developer-Modus: " + ('AN' if developer_mode_enabled else 'AUS'))

        payload = json.dumps({
            "ok": saved_ok,
            "error": "" if saved_ok else ("Speichern fehlgeschlagen: " + str(save_error)),
            "developer_mode": developer_mode_enabled
        }).encode('utf-8')

        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
        writer.write(b'Pragma: no-cache\r\n')
        writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(payload)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/emergency-delete-main':
        confirm = query_params.get('confirm', '')
        if not confirm:
            confirm = body_params.get('confirm', '')
        if confirm != '1':
            payload = json.dumps({
                "ok": False,
                "error": "Bestaetigung fehlt",
            }).encode('utf-8')
            writer.write(b'HTTP/1.1 400 Bad Request\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(payload)
            return True, trick_tuning_profile, developer_mode_enabled, language_code

        await perform_emergency_delete_main(writer)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/emergency-delete-boot':
        confirm = query_params.get('confirm', '')
        if not confirm:
            confirm = body_params.get('confirm', '')
        if confirm != '1':
            payload = json.dumps({
                "ok": False,
                "error": "Bestaetigung fehlt",
            }).encode('utf-8')
            writer.write(b'HTTP/1.1 400 Bad Request\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(payload)
            return True, trick_tuning_profile, developer_mode_enabled, language_code

        await perform_emergency_delete_boot(writer)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/set-language':
        selected = query_params.get('lang', '').strip().lower()
        if not selected:
            selected = body_params.get('lang', '').strip().lower()

        if not selected or not is_allowed_language(selected):
            payload = json.dumps({
                "ok": False,
                "error": "Sprache nicht verfuegbar",
            }).encode('utf-8')
            writer.write(b'HTTP/1.1 400 Bad Request\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(payload)
            return True, trick_tuning_profile, developer_mode_enabled, language_code

        language_code = selected
        set_language_code(language_code)
        saved_ok, save_error = save_system_settings(language=language_code)

        payload = json.dumps({
            "ok": saved_ok,
            "error": "" if saved_ok else ("Speichern fehlgeschlagen: " + str(save_error)),
            "language": language_code,
        }).encode('utf-8')
        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
        writer.write(b'Pragma: no-cache\r\n')
        writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(payload)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/confirm-highscore':
        await send_confirm_highscore_response(writer)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/reset-highscore':
        await send_reset_highscore_response(writer, query_params, body_params)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path in ('/clear-debug-log', '/clear-session-log') and request_method == 'POST':
        if query_params.get('confirm', '') != '1':
            payload = json.dumps({"ok": False, "error": "Bestaetigung fehlt"}).encode('utf-8')
            writer.write(b'HTTP/1.1 400 Bad Request\r\n')
        else:
            if request_path == '/clear-debug-log':
                for path in ('fpv_debug_session.txt', debug_export_file_path):
                    try:
                        os.remove(path)
                    except Exception:
                        pass
                init_debug_log_file()
                message = "Debug-Log geloescht"
            else:
                try:
                    os.remove(session_export_file_path)
                except Exception:
                    pass
                message = "Session-Log geloescht"
            payload = json.dumps({"ok": True, "message": message}).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store\r\n')
        writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(payload)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path in ('/download', '/download-session'):
        if enable_serial_debug:
            print("[DEBUG] [%ss] [DOWNLOAD-SESSION] Exportdatei wird erstellt" % (time.ticks_ms() // 1000))
        write_text_file(session_export_file_path, build_session_txt_content())
        await send_file_as_download(writer, session_export_file_path, "fpv_arcade_session.txt")
        if enable_serial_debug:
            print("[DEBUG] [%ss] [DOWNLOAD-SESSION] Datei versendet" % (time.ticks_ms() // 1000))
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path in ('/download-debug', '/download-debug-raw'):
        if enable_serial_debug:
            print("[DEBUG] [%ss] [DOWNLOAD-DEBUG] Exportdatei wird erstellt" % (time.ticks_ms() // 1000))
        build_debug_export_file()
        await send_file_as_download(writer, debug_export_file_path, "fpv_debug_log.txt")
        if enable_serial_debug:
            print("[DEBUG] [%ss] [DOWNLOAD-DEBUG] Datei versendet" % (time.ticks_ms() // 1000))
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    if request_path == '/simulate-trick':
        trick_kind = query_params.get('type', 'roll').strip().lower()
        if trick_kind not in ('roll', 'flip', 'spin'):
            trick_kind = 'roll'

        score_before = detector.score
        debug_console_only("[SIMULATE] Starte Trick-Simulation: " + trick_kind)
        await simulate_trick(trick_kind)
        points_gained = detector.score - score_before

        payload = json.dumps({
            "ok": True,
            "type": trick_kind,
            "trick": detector.last_trick_name if points_gained > 0 else None,
            "points": points_gained,
            "score": detector.score
        }).encode('utf-8')

        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
        writer.write(b'Pragma: no-cache\r\n')
        writer.write(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(payload)
        return True, trick_tuning_profile, developer_mode_enabled, language_code

    return False, trick_tuning_profile, developer_mode_enabled, language_code
