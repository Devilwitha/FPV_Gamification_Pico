"""OTA-Upload-Handler (ausgelagert aus main.py).

Enthaelt die Chunk-basierten Upload-Routen (/prepare-upload, /upload-chunk,
/finalize-upload, /restart-pico) sowie den Notaus-Loeschmechanismus
(/emergency-delete-*). Dieser Codeblock (~350 Zeilen) wurde aus main.py
extrahiert, weil er main.py's eigene Kompiliergroesse waehrend des riskanten
`import main` in boot.py unnoetig vergroesserte (siehe wiederholte
Speicherfragmentierungs-Crashes: "memory allocation failed" waehrend
`import main`, obwohl viel Heap frei war - MicroPython's `import`
kompiliert IMMER das komplette Zielmodul). Wird jetzt lazy importiert,
erst beim ersten tatsaechlichen Upload-/Restart-/Notaus-Request
(siehe main.py's _get_upload_helpers()).

Alle Funktionen erhalten benoetigte main.py-Objekte explizit ueber `deps`
(dict) bzw. `ota_state` (mutable dict mit den vier OTA-Chunk-Statuswerten -
wird BY REFERENCE durchgereicht, main.py und dieses Modul teilen sich also
denselben dict).
"""
import json
import os
import asyncio
import machine
import gc


def cleanup_update_artifacts(
    managed_targets,
    deps,
    remove_fixed_files=True,
    remove_txt_and_non_en_pak=False,
    remove_target_files=None,
):
    """Entfernt nur bekannte Update-Reste fuer Firmware-Dateien.

    WICHTIG: Keine Benutzerdaten loeschen.
        - Geloescht werden nur feste OTA-Tempdateien sowie alte Legacy-Backups
            (*.bak/main_backup.py) und *.bndl_tmp, deren Basisdatei in
            managed_targets enthalten ist.
        - Optional (remove_txt_and_non_en_pak=True): entferne zusaetzlich
            alle .txt-Dateien ausser firmware_version.txt sowie alle
            .pak-Dateien.
        - Optional (remove_target_files): entferne diese konkreten Dateien
            vor dem Upload, um Platz fuer das neue Bundle zu schaffen.
    - Nutzerdateien wie Profile (*.pro), Ausweisdateien (FPV_Ausweiss.*),
      Highscore/Settings/COPIL bleiben unberuehrt.
    """
    debug_log = deps["debug_log"]
    ota_staging_path = deps["ota_staging_path"]
    ota_bundle_target = deps["ota_bundle_target"]
    ota_lang_bundle_target = deps["ota_lang_bundle_target"]
    firmware_version_file = deps["firmware_version_file"]

    managed_set = set(managed_targets)
    remove_target_set = set(remove_target_files or ())

    if remove_fixed_files:
        for fixed_name in ("update.pbp", ota_staging_path, ota_bundle_target, ota_lang_bundle_target):
            try:
                os.remove(fixed_name)
                debug_log(f"[OTA CLEANUP] Entfernt: {fixed_name}")
            except Exception:
                pass

    try:
        entries = os.listdir()
    except Exception:
        entries = []

    for name in entries:
        remove = False

        if name in remove_target_set:
            remove = True

        if remove_txt_and_non_en_pak:
            if name.endswith('.txt') and name != firmware_version_file:
                remove = True
            elif name.endswith('.pak'):
                remove = True

        if name == "main_backup.py" and "main.py" in managed_set:
            remove = True
        elif name.endswith('.bak'):
            base = name[:-4]
            if base in managed_set:
                remove = True
        elif name.endswith('.bndl_tmp'):
            base = name[:-9]
            if base in managed_set:
                remove = True

        if not remove:
            continue

        try:
            os.remove(name)
            debug_log(f"[OTA CLEANUP] Entfernt: {name}")
        except Exception:
            pass


async def handle_upload_chunk(writer, body_text, body_params, ota_state, deps):
    url_decode = deps["url_decode"]
    debug_log = deps["debug_log"]
    developer_mode_enabled = deps["developer_mode_enabled"]
    ota_bundle_target = deps["ota_bundle_target"]
    ota_lang_bundle_target = deps["ota_lang_bundle_target"]
    ota_allowed_targets = deps["ota_allowed_targets"]

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
        else:
            chunk_data = body_params.get('data', '')
    else:
        chunk_data = body_params.get('data', '')

    try:
        chunk_index = int(chunk_index_str)
        total = int(total_str)
        target_valid = True
        target_error = ""

        if chunk_index == 0:
            if target_str == ota_bundle_target or target_str == ota_lang_bundle_target:
                target_valid = True
            elif target_str in ota_allowed_targets:
                target_valid = developer_mode_enabled
                if not target_valid:
                    target_error = "Einzeldatei-Uploads sind deaktiviert. Aktiviere den Developer-Modus (System-Seite) oder lade ein firmware.nbo Bundle hoch."
            else:
                target_valid = False

            if target_valid:
                ota_state["total_chunks"] = total
                ota_state["received_chunks"] = 0
                ota_state["update_active"] = True
                ota_state["target_file"] = target_str
                # Vor einem neuen OTA-Lauf alte Update-Artefakte entfernen,
                # um Speicher freizugeben (Benutzerdaten bleiben unberuehrt).
                cleanup_update_artifacts(
                    ota_allowed_targets,
                    deps,
                    remove_fixed_files=False,
                    remove_txt_and_non_en_pak=True,
                )
                try:
                    os.remove('update.pbp')
                except Exception:
                    pass
                debug_log(f"[OTA] Chunk-Transfer gestartet: {total} Chunks erwartet, Ziel={target_str}")

        if not target_valid:
            response = json.dumps({"ok": False, "error": target_error or f"Ungueltiges Ziel: {target_str}"}).encode('utf-8')
            writer.write(b'HTTP/1.1 400 Bad Request\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response)
        else:
            if chunk_data:
                with open('update.pbp', 'a') as f:
                    f.write(chunk_data)
                ota_state["received_chunks"] += 1
                debug_log(f"[OTA] Chunk {chunk_index+1}/{total} empfangen ({len(chunk_data)} bytes)")

            response = json.dumps({"ok": True, "message": f"Chunk {chunk_index+1}/{total} gespeichert"}).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response)

            if chunk_index + 1 == total and ota_state["received_chunks"] == ota_state["total_chunks"]:
                debug_log("[OTA] Alle Chunks empfangen, bitte /finalize-upload aufrufen")

        # Nach jedem Chunk aufraeumen: bei ~200+ einzelnen HTTP-Requests in
        # Folge (grosse .nbo Bundles) sammelt sich sonst Heap-Fragmentierung
        # an, die spaeter im Upload zu einem MemoryError/Absturz fuehren
        # kann (Verbindung bricht dann fuer den Browser als "Failed to
        # fetch" ab). Siehe main.py's send_html_file() fuer denselben Grund.
        gc.collect()

    except Exception as e:
        debug_log(f"[OTA CHUNK] Fehler: {e}")
        ota_state["update_active"] = False
        response = json.dumps({"ok": False, "error": str(e)}).encode('utf-8')
        writer.write(b'HTTP/1.1 400 Bad Request\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response)


async def handle_prepare_upload(writer, query_params, body_params, ota_state, deps):
    debug_log = deps["debug_log"]
    developer_mode_enabled = deps["developer_mode_enabled"]
    ota_bundle_target = deps["ota_bundle_target"]
    ota_lang_bundle_target = deps["ota_lang_bundle_target"]
    ota_allowed_targets = deps["ota_allowed_targets"]

    target_str = query_params.get('target', '').strip()
    if not target_str:
        target_str = body_params.get('target', '').strip()

    mode = query_params.get('bundle_mode', '').strip().lower()
    if not mode:
        mode = body_params.get('bundle_mode', '').strip().lower()
    if mode not in ('complete', 'light', 'recovery', 'language'):
        mode = 'light'

    entries_raw = query_params.get('entries', '').strip()
    if not entries_raw:
        entries_raw = body_params.get('entries', '').strip()

    bundle_entries = []
    if entries_raw:
        parts = entries_raw.split(',')
        for part in parts:
            name = part.strip()
            if not name:
                continue
            if name not in bundle_entries:
                bundle_entries.append(name)

    target_valid = True
    target_error = ""
    if target_str == ota_bundle_target or target_str == ota_lang_bundle_target:
        target_valid = True
    elif target_str in ota_allowed_targets:
        target_valid = developer_mode_enabled
        if not target_valid:
            target_error = "Einzeldatei-Uploads sind deaktiviert. Aktiviere den Developer-Modus (System-Seite) oder lade ein firmware.nbo Bundle hoch."
    else:
        target_valid = False

    if not target_valid:
        response = json.dumps({"ok": False, "error": target_error or f"Ungueltiges Ziel: {target_str}"}).encode('utf-8')
        writer.write(b'HTTP/1.1 400 Bad Request\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response)
        return

    preserve_user_files = {'copil'}
    if mode == 'complete':
        remove_targets = [name for name in ota_allowed_targets if name not in preserve_user_files]
    elif bundle_entries:
        remove_targets = [
            name for name in bundle_entries
            if (name in ota_allowed_targets) and (name not in preserve_user_files)
        ]
    else:
        remove_targets = [
            target_str
            for _ in (0,)
            if (target_str in ota_allowed_targets) and (target_str not in preserve_user_files)
        ]

    # Vor Empfang der ersten Upload-Chunks aufraeumen, um maximalen Platz
    # fuer das kommende firmware.nbo/lang.pak zu schaffen.
    cleanup_update_artifacts(
        ota_allowed_targets,
        deps,
        remove_fixed_files=True,
        remove_txt_and_non_en_pak=True,
        remove_target_files=remove_targets,
    )
    ota_state["total_chunks"] = 0
    ota_state["received_chunks"] = 0
    ota_state["update_active"] = False
    ota_state["target_file"] = target_str

    response = json.dumps({
        "ok": True,
        "target": target_str,
        "bundle_mode": mode,
        "removed_targets": len(remove_targets),
        "message": "Cleanup vor Upload abgeschlossen",
    }).encode('utf-8')
    writer.write(b'HTTP/1.1 200 OK\r\n')
    writer.write(b'Content-Type: application/json\r\n')
    writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
    writer.write(b'Pragma: no-cache\r\n')
    writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
    writer.write(b'Connection: close\r\n\r\n')
    writer.write(response)


async def handle_finalize_upload(writer, ota_state, deps):
    debug_log = deps["debug_log"]
    ota_bundle_target = deps["ota_bundle_target"]
    ota_lang_bundle_target = deps["ota_lang_bundle_target"]
    ota_allowed_targets = deps["ota_allowed_targets"]
    ota_staging_path = deps["ota_staging_path"]
    apply_firmware_bundle_from_base64 = deps["apply_firmware_bundle_from_base64"]
    safe_base64_file_to_file = deps["safe_base64_file_to_file"]

    try:
        debug_log(f"[OTA] Finalisierung: {ota_state['received_chunks']}/{ota_state['total_chunks']} Chunks vorhanden")
        if ota_state["received_chunks"] != ota_state["total_chunks"]:
            raise Exception(f"Incomplete upload: {ota_state['received_chunks']}/{ota_state['total_chunks']}")

        ota_target_file = ota_state["target_file"]
        is_bundle = (ota_target_file == ota_bundle_target or ota_target_file == ota_lang_bundle_target)
        target = ota_target_file if (is_bundle or ota_target_file in ota_allowed_targets) else "main.py"

        if is_bundle:
            # Bundle wird DIREKT aus der noch base64-kodierten 'update.pbp'
            # gestreamt entpackt (apply_firmware_bundle_from_base64()) -
            # OHNE zuerst den kompletten Bundle-Inhalt nach ota_staging_path
            # zu dekodieren. Das vermeidet, dass 'update.pbp' (Base64-Text,
            # ca. 1.33x groesser als der Bundle-Inhalt) UND eine komplett
            # dekodierte Kopie des Bundles gleichzeitig auf dem Flash liegen
            # - genau das fuehrte bei wachsenden Bundles zu OSError 28
            # (ENOSPC, kein Speicherplatz mehr frei).
            extracted_files, needs_restart = apply_firmware_bundle_from_base64('update.pbp')
            try:
                os.remove('update.pbp')
            except Exception:
                pass
            cleanup_update_artifacts(ota_allowed_targets, deps)
            message = f"Firmware-Bundle angewendet: {len(extracted_files)} Datei(en) ersetzt ({', '.join(extracted_files)})"
            if needs_restart:
                message += " Starte Neustart..."
        else:
            decode_ok = safe_base64_file_to_file('update.pbp', ota_staging_path)
            if decode_ok is not True:
                raise Exception(f"Base64 Dekodierung fehlgeschlagen: {decode_ok}")

            try:
                os.remove('update.pbp')
            except Exception:
                pass

            try:
                staged_size = os.stat(ota_staging_path)[6]
                debug_log(f"[OTA] Staging-Datei: {staged_size} bytes (Ziel: {target})")
            except Exception:
                staged_size = 0

            try:
                os.remove(target)
            except Exception:
                pass

            os.rename(ota_staging_path, target)
            debug_log(f"[OTA] Finale Datei gespeichert: {target} ({staged_size} bytes)")

            needs_restart = (target == "main.py")
            cleanup_update_artifacts(ota_allowed_targets, deps)
            message = f"Update erfolgreich gespeichert: {target} ({staged_size} bytes)!"
            if needs_restart:
                message += " Starte Neustart..."

        response = json.dumps({"ok": True, "message": message, "restart": needs_restart}).encode('utf-8')
        writer.write(b'HTTP/1.1 200 OK\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response)

        ota_state["total_chunks"] = 0
        ota_state["received_chunks"] = 0
        ota_state["update_active"] = False
        cleanup_update_artifacts(ota_allowed_targets, deps)

        try:
            await writer.drain()
        except Exception:
            pass

        if needs_restart:
            await asyncio.sleep_ms(2000)
            debug_log("[OTA] Starte machine.reset()...")
            machine.reset()

    except Exception as e:
        debug_log(f"[OTA FINALIZE] Fehler: {str(e)[:100]}")
        response = json.dumps({"ok": False, "error": str(e)[:100]}).encode('utf-8')
        writer.write(b'HTTP/1.1 500 Internal Server Error\r\n')
        writer.write(b'Content-Type: application/json\r\n')
        writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
        writer.write(b'Connection: close\r\n\r\n')
        writer.write(response)
        ota_state["total_chunks"] = 0
        ota_state["received_chunks"] = 0
        ota_state["update_active"] = False
        cleanup_update_artifacts(ota_allowed_targets, deps)


async def handle_restart_pico(writer, deps):
    debug_log = deps["debug_log"]
    response = json.dumps({"ok": True, "message": "Pico startet neu..."}).encode('utf-8')
    writer.write(b'HTTP/1.1 200 OK\r\n')
    writer.write(b'Content-Type: application/json\r\n')
    writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
    writer.write(b'Connection: close\r\n\r\n')
    writer.write(response)
    try:
        await writer.drain()
    except Exception:
        pass
    await asyncio.sleep_ms(1000)
    debug_log("[RESTART] machine.reset() wird aufgerufen...")
    machine.reset()


async def handle_emergency_delete_target(writer, target_file, deps):
    debug_log = deps["debug_log"]
    deleted = []
    for path in (target_file,):
        try:
            os.remove(path)
            deleted.append(path)
            debug_log("[EMERGENCY] Geloescht: " + path)
        except Exception as e:
            debug_log("[EMERGENCY] Konnte nicht loeschen: %s (%s)" % (path, e))

    message = "Notaus ausgefuehrt (%s). Neustart laeuft." % target_file
    response = json.dumps({"ok": True, "message": message, "deleted": deleted}).encode('utf-8')
    writer.write(b'HTTP/1.1 200 OK\r\n')
    writer.write(b'Content-Type: application/json\r\n')
    writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
    writer.write(b'Connection: close\r\n\r\n')
    writer.write(response)
    try:
        await writer.drain()
    except Exception:
        pass
    await asyncio.sleep_ms(500)
    machine.reset()


async def handle_emergency_delete_main(writer, deps):
    await handle_emergency_delete_target(writer, "main.py", deps)


async def handle_emergency_delete_boot(writer, deps):
    await handle_emergency_delete_target(writer, "boot.py", deps)
