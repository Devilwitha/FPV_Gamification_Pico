"""zip_helpers.py - minimaler ZIP-Entpacker fuer den Plugin-Upload
("/admin-plugins"-Seite, siehe pico_web_api.py).

MicroPython bringt kein zipfile-Modul mit - dieses Modul liest daher die
End-of-Central-Directory sowie das zentrale Verzeichnis selbst per
struct.unpack() (minimaler PKZIP-Parser) und entpackt komprimierte Eintraege
(Kompressionsmethode 8/DEFLATE) ueber MicroPythons eingebautes deflate-Modul
im RAW-Modus - exakt das Format, das ZIP fuer Methode 8 verwendet. Methode 0
(ungespeichert/STORED) wird unveraendert uebernommen.

Unterordner in der ZIP werden IGNORIERT (gleiches flache Verhalten wie
webshop/app.py's _process_plugin_zip_upload()): jeder Dateieintrag landet
unter seinem Basisnamen direkt in mods/<name>/, unabhaengig vom Pfad
innerhalb der ZIP.

<name> kommt NICHT aus der ZIP (z.B. manifest.json), sondern vom Aufrufer
(pico_web_api.py leitet ihn aus dem hochgeladenen Dateinamen ab, z.B.
"shooter.zip" -> mods/shooter/) - siehe extract_plugin_zip().

Lazy importiert aus pico_web_api.py, erst beim ersten tatsaechlichen
Plugin-ZIP-Upload.
"""

import gc
import io
import json
import os
import struct

try:
    from main import debug_log as _debug_log
except Exception:
    def _debug_log(message):
        try:
            print("[ZIP] {}".format(message))
        except Exception:
            pass

EOCD_SIGNATURE = b"PK\x05\x06"
CENTRAL_DIR_SIGNATURE = b"PK\x01\x02"
LOCAL_HEADER_SIGNATURE = b"PK\x03\x04"

# Bytelayout siehe PKZIP-APPNOTE.TXT: '<' = little-endian, 'H' = 2 Byte,
# 'I' = 4 Byte, '4s' = 4-Byte-Signatur.
EOCD_STRUCT = "<4s4HIIH"          # 22 Byte
CENTRAL_DIR_STRUCT = "<4s6H3I5H2I"  # 46 Byte (bis inkl. lokalem Header-Offset)
LOCAL_HEADER_STRUCT = "<4s5H3I2H"   # 30 Byte (bis inkl. Extra-Feld-Laenge)

MAX_EOCD_SEARCH = 65557  # 22 Byte EOCD + maximal 65535 Byte ZIP-Kommentar


def _find_eocd_offset(f, file_size):
    search_size = min(file_size, MAX_EOCD_SEARCH)
    f.seek(file_size - search_size)
    tail = f.read(search_size)
    idx = tail.rfind(EOCD_SIGNATURE)
    if idx < 0:
        raise ValueError("Keine gueltige ZIP-Datei (End-of-Central-Directory nicht gefunden)")
    return (file_size - search_size) + idx


def _list_entries(f):
    """Liefert eine Liste von (basename, local_header_offset,
    compression_method, compressed_size) fuer jeden Dateieintrag (reine
    Ordnereintraege/leere Basename werden uebersprungen)."""
    f.seek(0, 2)
    file_size = f.tell()
    eocd_offset = _find_eocd_offset(f, file_size)

    f.seek(eocd_offset)
    eocd = f.read(22)
    (_sig, _disk, _cd_disk, _entries_this_disk, total_entries,
     _cd_size, cd_offset, _comment_len) = struct.unpack(EOCD_STRUCT, eocd)

    entries = []
    f.seek(cd_offset)
    for _ in range(total_entries):
        header = f.read(46)
        if len(header) < 46 or header[:4] != CENTRAL_DIR_SIGNATURE:
            raise ValueError("Beschaedigtes ZIP-Zentralverzeichnis")
        (_sig, _ver_made_by, _ver_needed, _flag, method, _mtime, _mdate,
         _crc32, comp_size, _uncomp_size, name_len, extra_len, comment_len,
         _disk_num, _int_attr, _ext_attr, local_offset) = struct.unpack(CENTRAL_DIR_STRUCT, header)

        raw_name = f.read(name_len)
        f.seek(extra_len + comment_len, 1)

        try:
            name = raw_name.decode("utf-8")
        except Exception:
            continue

        if name.endswith("/"):
            continue
        basename = name.replace("\\", "/").rsplit("/", 1)[-1]
        if not basename or basename.startswith("."):
            continue
        entries.append((basename, local_offset, method, comp_size))
    return entries


def _read_entry_data(f, local_offset, method, comp_size):
    f.seek(local_offset)
    header = f.read(30)
    if len(header) < 30 or header[:4] != LOCAL_HEADER_SIGNATURE:
        raise ValueError("Beschaedigter lokaler ZIP-Header")
    (_sig, _ver_needed, _flag, _method, _mtime, _mdate, _crc32,
     _comp_size, _uncomp_size, name_len, extra_len) = struct.unpack(LOCAL_HEADER_STRUCT, header)

    f.seek(local_offset + 30 + name_len + extra_len)
    raw = f.read(comp_size)

    if method == 0:
        return raw
    if method == 8:
        import deflate
        with deflate.DeflateIO(io.BytesIO(raw), deflate.RAW) as d:
            return d.read()
    raise ValueError("Nicht unterstuetzte ZIP-Kompression (Methode {})".format(method))


def _entry_stem(entry_filename):
    if entry_filename.endswith(".py"):
        return entry_filename[:-3]
    if entry_filename.endswith(".mpy"):
        return entry_filename[:-4]
    return entry_filename


def extract_plugin_zip(zip_path, plugin_name):
    """Entpackt zip_path FLACH nach mods/<plugin_name>/ - ein bereits
    vorhandener gleichnamiger Ordner wird vorher komplett ersetzt (per
    plugin_manager.delete_plugin(), das ein aktives Plugin zuerst sauber
    deaktiviert/entlaedt). Prueft NACH dem Entpacken, aber VOR dem
    Aktivieren, dass die in manifest.json's "entry" (Standard "main.py")
    genannte Einstiegsdatei tatsaechlich unter ihrem exakten Namen dabei war
    - eine ZIP mit z.B. nur "Main.py" (Gross-/Kleinschreibung), einem
    abweichenden Dateinamen oder generell fehlender Einstiegsdatei wuerde
    sonst erst tief in plugin_manager.load_single_plugin() als generischer
    Crash ("has_error") landen, was pico_web_api.py's Aufrufer frueher
    faelschlich trotzdem als Erfolg gemeldet hat. Aktiviert das Plugin
    danach ueber plugin_manager.load_single_plugin() und gibt die Liste der
    tatsaechlich geschriebenen Dateinamen zurueck (Diagnose-Hilfe fuer den
    Aufrufer, siehe pico_web_api.py's _handle_plugin_upload_finalize()).
    Wirft bei jedem Fehler eine Exception mit verstaendlicher Meldung."""
    import plugin_manager

    with open(zip_path, "rb") as f:
        entries = _list_entries(f)
        if not entries:
            raise ValueError("ZIP-Datei enthaelt keine Dateien")
        written_names = [name for name, _, _, _ in entries]
        if "manifest.json" not in written_names:
            raise ValueError("ZIP enthaelt keine manifest.json")

        plugin_manager.delete_plugin(plugin_name)

        try:
            os.mkdir("mods")
        except Exception:
            pass
        try:
            os.mkdir("mods/" + plugin_name)
        except Exception:
            pass

        target_dir = "mods/" + plugin_name
        manifest_bytes = None
        for name, local_offset, method, comp_size in entries:
            data = _read_entry_data(f, local_offset, method, comp_size)
            if name == "manifest.json":
                manifest_bytes = data
            written_bytes = len(data)
            with open(target_dir + "/" + name, "wb") as out_file:
                out_file.write(data)
            del data
            gc.collect()
            _debug_log("[ZIP] {}/{} geschrieben ({} Bytes, Methode {})".format(
                target_dir, name, written_bytes, method))

    try:
        manifest_entry = json.loads(manifest_bytes.decode("utf-8")).get("entry") or "main.py"
    except Exception:
        manifest_entry = "main.py"
    entry_stem = _entry_stem(str(manifest_entry))
    has_entry_file = (entry_stem + ".py") in written_names or (entry_stem + ".mpy") in written_names
    if not has_entry_file:
        raise ValueError(
            "ZIP enthaelt keine Einstiegsdatei '{}.py'/'{}.mpy' (manifest.json's 'entry') - "
            "enthaltene Dateien: {}".format(entry_stem, entry_stem, ", ".join(written_names))
        )

    # Zusaetzlich zur reinen ZIP-Inhaltspruefung oben (has_entry_file, prueft
    # nur was IN der ZIP war) hier nochmal GEGEN DIE ECHTE PLATTE pruefen -
    # ein real beobachteter Fall zeigte "ok" beim Entpacken, aber trotzdem
    # "No module named" beim direkt folgenden Import, siehe Docstring oben.
    # Diese Pruefung deckt auf, ob die Datei tatsaechlich persistiert wurde
    # (statt z.B. an einem stillen Schreibfehler zu scheitern) und liefert im
    # Fehlerfall eine echte Verzeichnis-Momentaufnahme statt eines
    # kryptischen ModuleNotFoundError tief in plugin_manager.
    entry_on_disk = False
    for candidate_ext in (".py", ".mpy"):
        try:
            os.stat(target_dir + "/" + entry_stem + candidate_ext)
            entry_on_disk = True
            break
        except Exception:
            pass
    if not entry_on_disk:
        try:
            actual_files = os.listdir(target_dir)
        except Exception as e:
            actual_files = ["<os.listdir fehlgeschlagen: {}>".format(e)]
        _debug_log("[ZIP] '{}' fehlt nach dem Schreiben auf der Platte - tatsaechlicher Inhalt von {}: {}".format(
            entry_stem, target_dir, actual_files))
        raise ValueError(
            "Einstiegsdatei '{}.py'/'{}.mpy' wurde aus der ZIP gelesen, ist aber nach dem Schreiben "
            "nicht auf der Platte auffindbar (Speicherplatz voll?) - tatsaechlich vorhanden: {}".format(
                entry_stem, entry_stem, ", ".join(actual_files) or "(leer)")
        )

    plugin_manager.load_single_plugin(plugin_name)
    return written_names
