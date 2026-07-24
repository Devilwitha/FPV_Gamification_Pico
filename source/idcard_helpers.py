import gc
import json
import os
try:
    import binascii
except Exception:
    import ubinascii as binascii

FPV_AUSWEISS_BASENAME = "FPV_Ausweiss"
FPV_AUSWEISS_JPG_PATH = FPV_AUSWEISS_BASENAME + ".jpg"
FPV_AUSWEISS_PNG_PATH = FPV_AUSWEISS_BASENAME + ".png"
FPV_AUSWEISS_PDF_PATH = FPV_AUSWEISS_BASENAME + ".pdf"
FPV_AUSWEISS_UPLOAD_BASE64_PATH = "fpv_ausweiss_upload.pbp"
FPV_AUSWEISS_UPLOAD_BIN_PATH = "fpv_ausweiss_upload.bin"

_upload_total_chunks = 0
_upload_received_chunks = 0
_upload_ext = ""


def _remove_if_exists(path):
    try:
        os.remove(path)
    except Exception:
        pass


def _get_existing_path_and_type():
    try:
        os.stat(FPV_AUSWEISS_JPG_PATH)
        return FPV_AUSWEISS_JPG_PATH, "image/jpeg"
    except Exception:
        pass

    try:
        os.stat(FPV_AUSWEISS_PNG_PATH)
        return FPV_AUSWEISS_PNG_PATH, "image/png"
    except Exception:
        pass

    try:
        os.stat(FPV_AUSWEISS_PDF_PATH)
        return FPV_AUSWEISS_PDF_PATH, "application/pdf"
    except Exception:
        pass

    return None, None


def _detect_format(file_path):
    try:
        with open(file_path, "rb") as f:
            head = f.read(16)
    except Exception:
        return None

    if len(head) >= 8 and head[0:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if len(head) >= 2 and head[0] == 0xFF and head[1] == 0xD8:
        return "jpg"
    if len(head) >= 5 and head[0:5] == b"%PDF-":
        return "pdf"
    return None


def _reset_upload_state():
    global _upload_total_chunks, _upload_received_chunks, _upload_ext
    _upload_total_chunks = 0
    _upload_received_chunks = 0
    _upload_ext = ""
    _remove_if_exists(FPV_AUSWEISS_UPLOAD_BASE64_PATH)
    _remove_if_exists(FPV_AUSWEISS_UPLOAD_BIN_PATH)


def _decode_b64_chunk(chunk_data):
    # Chunk ist bereits URL-dekodiert (enthaelt nur Base64-Zeichen inkl. '=').
    raw = chunk_data.encode("ascii")
    return binascii.a2b_base64(raw)


def _format_storage_error(e):
    try:
        if isinstance(e, OSError) and len(e.args) > 0 and int(e.args[0]) == 28:
            return "Kein Speicherplatz frei auf dem Pico-Dateisystem (OSError 28)."
    except Exception:
        pass
    return str(e)


async def _handle_status(writer):
    path, mime = _get_existing_path_and_type()
    exists = path is not None
    size = 0
    if exists:
        try:
            size = int(os.stat(path)[6])
        except Exception:
            size = 0

    payload = json.dumps({
        "ok": True,
        "exists": exists,
        "filename": path if exists else "",
        "mime": mime if exists else "",
        "size": size,
    }).encode("utf-8")

    writer.write(b"HTTP/1.1 200 OK\r\n")
    writer.write(b"Content-Type: application/json\r\n")
    writer.write(b"Cache-Control: no-store, no-cache, must-revalidate\r\n")
    writer.write(b"Pragma: no-cache\r\n")
    writer.write(b"Content-Length: " + str(len(payload)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(payload)


async def _handle_image(writer):
    path, mime = _get_existing_path_and_type()
    if path is None:
        payload = json.dumps({"ok": False, "error": "Kein FPV-Ausweis gespeichert"}).encode("utf-8")
        writer.write(b"HTTP/1.1 404 Not Found\r\n")
        writer.write(b"Content-Type: application/json\r\n")
        writer.write(b"Content-Length: " + str(len(payload)).encode() + b"\r\n")
        writer.write(b"Connection: close\r\n\r\n")
        writer.write(payload)
        return

    file_size = os.stat(path)[6]
    writer.write(b"HTTP/1.1 200 OK\r\n")
    writer.write(b"Content-Type: " + mime.encode("utf-8") + b"\r\n")
    writer.write(b"Cache-Control: no-store, no-cache, must-revalidate\r\n")
    writer.write(b"Pragma: no-cache\r\n")
    writer.write(b"Content-Length: " + str(file_size).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")

    with open(path, "rb") as f:
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


async def _handle_delete(writer):
    _remove_if_exists(FPV_AUSWEISS_JPG_PATH)
    _remove_if_exists(FPV_AUSWEISS_PNG_PATH)
    _remove_if_exists(FPV_AUSWEISS_PDF_PATH)
    _reset_upload_state()

    payload = json.dumps({"ok": True, "message": "FPV-Ausweis geloescht"}).encode("utf-8")
    writer.write(b"HTTP/1.1 200 OK\r\n")
    writer.write(b"Content-Type: application/json\r\n")
    writer.write(b"Cache-Control: no-store, no-cache, must-revalidate\r\n")
    writer.write(b"Pragma: no-cache\r\n")
    writer.write(b"Content-Length: " + str(len(payload)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(payload)


async def _handle_upload_chunk(writer, body_text, body_params, url_decode):
    global _upload_total_chunks, _upload_received_chunks, _upload_ext

    chunk_index_str = "-1"
    total_str = "0"
    ext_str = ""

    if body_text:
        idx_pos = body_text.find("index=")
        if idx_pos >= 0:
            idx_start = idx_pos + 6
            idx_end = body_text.find("&", idx_start)
            if idx_end < 0:
                idx_end = len(body_text)
            chunk_index_str = url_decode(body_text[idx_start:idx_end])

        total_pos = body_text.find("total=")
        if total_pos >= 0:
            total_start = total_pos + 6
            total_end = body_text.find("&", total_start)
            if total_end < 0:
                total_end = len(body_text)
            total_str = url_decode(body_text[total_start:total_end])

        ext_pos = body_text.find("ext=")
        if ext_pos >= 0:
            ext_start = ext_pos + 4
            ext_end = body_text.find("&", ext_start)
            if ext_end < 0:
                ext_end = len(body_text)
            ext_str = url_decode(body_text[ext_start:ext_end])

    if not ext_str:
        ext_str = body_params.get("ext", "")

    chunk_data = ""
    if body_text:
        marker = "&data="
        pos = body_text.find(marker)
        if pos >= 0:
            chunk_data = url_decode(body_text[pos + len(marker):])
        elif body_text.startswith("data="):
            chunk_data = url_decode(body_text[5:])
    if not chunk_data:
        chunk_data = body_params.get("data", "")

    try:
        chunk_index = int(chunk_index_str)
        total = int(total_str)
        ext = str(ext_str or "").strip().lower()
        if ext == "jpeg":
            ext = "jpg"

        if total <= 0:
            raise Exception("Ungueltige Chunk-Anzahl")

        if chunk_index == 0:
            if ext not in ("jpg", "png", "pdf"):
                raise Exception("Nur JPG/JPEG, PNG oder PDF erlaubt")
            _upload_total_chunks = total
            _upload_received_chunks = 0
            _upload_ext = ext
            _remove_if_exists(FPV_AUSWEISS_UPLOAD_BASE64_PATH)
            _remove_if_exists(FPV_AUSWEISS_UPLOAD_BIN_PATH)

        if chunk_data:
            binary_chunk = _decode_b64_chunk(chunk_data)
            with open(FPV_AUSWEISS_UPLOAD_BIN_PATH, "ab") as f:
                f.write(binary_chunk)
            _upload_received_chunks += 1

        payload = json.dumps({"ok": True, "message": "Chunk %d/%d gespeichert" % (chunk_index + 1, total)}).encode("utf-8")
        writer.write(b"HTTP/1.1 200 OK\r\n")
        writer.write(b"Content-Type: application/json\r\n")
        writer.write(b"Content-Length: " + str(len(payload)).encode() + b"\r\n")
        writer.write(b"Connection: close\r\n\r\n")
        writer.write(payload)
        gc.collect()
    except Exception as e:
        _reset_upload_state()
        payload = json.dumps({"ok": False, "error": _format_storage_error(e)}).encode("utf-8")
        writer.write(b"HTTP/1.1 400 Bad Request\r\n")
        writer.write(b"Content-Type: application/json\r\n")
        writer.write(b"Content-Length: " + str(len(payload)).encode() + b"\r\n")
        writer.write(b"Connection: close\r\n\r\n")
        writer.write(payload)


async def _handle_finalize(writer, safe_base64_file_to_file):
    global _upload_total_chunks, _upload_received_chunks, _upload_ext

    try:
        if _upload_total_chunks <= 0:
            raise Exception("Kein Ausweis-Upload gestartet")
        if _upload_received_chunks != _upload_total_chunks:
            raise Exception("Upload unvollstaendig: %d/%d" % (_upload_received_chunks, _upload_total_chunks))
        if _upload_ext not in ("jpg", "png", "pdf"):
            raise Exception("Ungueltiges Ausweis-Format")

        # Binardaten wurden bereits waehrend /idcard-upload-chunk geschrieben.
        try:
            os.stat(FPV_AUSWEISS_UPLOAD_BIN_PATH)
        except Exception:
            raise Exception("Upload-Daten fehlen oder sind unvollstaendig")

        detected_ext = _detect_format(FPV_AUSWEISS_UPLOAD_BIN_PATH)
        if detected_ext is None:
            raise Exception("Datei ist kein gueltiges JPG/PNG/PDF")
        if detected_ext != _upload_ext:
            raise Exception("Dateiinhalt passt nicht zur Endung")

        if detected_ext == "jpg":
            target_path = FPV_AUSWEISS_JPG_PATH
        elif detected_ext == "png":
            target_path = FPV_AUSWEISS_PNG_PATH
        else:
            target_path = FPV_AUSWEISS_PDF_PATH

        for old_path in (FPV_AUSWEISS_JPG_PATH, FPV_AUSWEISS_PNG_PATH, FPV_AUSWEISS_PDF_PATH):
            if old_path != target_path:
                _remove_if_exists(old_path)
        _remove_if_exists(target_path)
        os.rename(FPV_AUSWEISS_UPLOAD_BIN_PATH, target_path)

        size = 0
        try:
            size = int(os.stat(target_path)[6])
        except Exception:
            size = 0

        payload = json.dumps({
            "ok": True,
            "message": "FPV-Ausweis gespeichert: " + target_path,
            "filename": target_path,
            "size": size,
        }).encode("utf-8")
        writer.write(b"HTTP/1.1 200 OK\r\n")
        writer.write(b"Content-Type: application/json\r\n")
        writer.write(b"Content-Length: " + str(len(payload)).encode() + b"\r\n")
        writer.write(b"Connection: close\r\n\r\n")
        writer.write(payload)
        _reset_upload_state()
    except Exception as e:
        _reset_upload_state()
        payload = json.dumps({"ok": False, "error": _format_storage_error(e)}).encode("utf-8")
        writer.write(b"HTTP/1.1 500 Internal Server Error\r\n")
        writer.write(b"Content-Type: application/json\r\n")
        writer.write(b"Content-Length: " + str(len(payload)).encode() + b"\r\n")
        writer.write(b"Connection: close\r\n\r\n")
        writer.write(payload)


async def handle_idcard_route(writer, request_path, request_method, body_text, body_params, url_decode, safe_base64_file_to_file):
    if request_path == "/idcard-status":
        await _handle_status(writer)
        return True
    if request_path == "/idcard-image":
        await _handle_image(writer)
        return True
    if request_path == "/idcard-delete":
        await _handle_delete(writer)
        return True
    if request_path == "/idcard-upload-chunk" and request_method == "POST":
        await _handle_upload_chunk(writer, body_text, body_params, url_decode)
        return True
    if request_path == "/idcard-finalize":
        await _handle_finalize(writer, safe_base64_file_to_file)
        return True
    return False
