"""
build_firmware.py

Verpackt alle Firmware-Dateien (main.py + alle Admin-/HTML-Seiten) des
FPV_Gamification_Pico Projekts in eine einzelne Bundle-Datei "firmware.nbo".

Diese Datei kann anschliessend ueber den Admin-Bereich (/admin-update)
per OTA-Update in EINEM Rutsch auf den Pico hochgeladen werden - der
Pico entpackt das Bundle serverseitig und ersetzt alle enthaltenen
Dateien (main.py, index.html, admin_*.html) automatisch.

Nutzung (auf dem PC, mit normalem Python 3, NICHT auf dem Pico ausfuehren):
    python build_firmware.py              -> oeffnet die grafische Oberflaeche (GUI)
    python build_firmware.py [output_path] -> Kommandozeilen-Modus (kein Fenster)

Ohne Argument oeffnet sich ein Fenster, das die gefundenen Dateien auflistet
und per Knopfdruck ("Bundle erstellen") das firmware.nbo mit Fortschrittsbalken
baut. Mit Argument laeuft das Skript wie bisher rein auf der Kommandozeile
(z.B. fuer Automatisierung/Skripte).

Die GUI enthaelt zusaetzlich das Offline-Lizenzsystem (RSA-Hardware-Kopplung,
siehe source/license_verifier.py): der Knopf "Komplette Firmware inkl. Lizenz
bauen & installieren (seriell)" liest machine.unique_id() vom verbundenen
Pico, signiert eine passende license.lic (siehe license_generator.py),
kompiliert den gesamten Quellcode per mpy-cross zu .mpy (Quellcode-Schutz -
ausser boot.py/recovery.py, siehe MPY_EXCLUDED_FILES) und ueberspielt alles
inkl. license.lic + public_key.pem direkt per USB-Seriell.

Bundle-Format (einfach, ohne Abhaengigkeiten wie zipfile/tarfile, damit
main.py es mit reinem MicroPython + struct wieder einlesen kann):

    Offset  Groesse   Inhalt
    0       8 Bytes   Magic-Header b"FPVBNDL1"
    8       4 Bytes   Anzahl Dateien (big-endian uint32)
    ...     pro Datei:
              4 Bytes   Laenge des Dateinamens (big-endian uint32)
              N Bytes   Dateiname (UTF-8)
              4 Bytes   Laenge des Dateiinhalts (big-endian uint32)
              M Bytes   Dateiinhalt (roh, binaer)
"""
import os
import argparse
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import base64
import json
import hashlib
import tempfile
from datetime import datetime
from urllib import error, parse, request

try:
    from serial.tools import list_ports
except Exception:
    list_ports = None

# license_generator.py liegt im selben tools/-Ordner. Import optional/lazy
# per try-except, damit build_firmware.py (Bundle bauen/OTA-Upload) auch
# ohne installiertes 'cryptography'-Paket weiter funktioniert - nur die
# lizenzbezogenen GUI-Funktionen (siehe build_and_flash_with_license())
# brauchen es tatsaechlich.
try:
    import license_generator
except Exception:
    license_generator = None

# Dieses Skript liegt im tools/-Unterordner - PROJECT_ROOT ist daher das
# Elternverzeichnis von tools/, nicht der Ordner dieser Datei selbst.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DIR = os.path.join(PROJECT_ROOT, "source")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")
# RSA-Schluesselpaar fuer das Offline-Lizenzsystem (siehe license_generator.py/
# source/license_verifier.py). private_key.pem verlaesst diesen Ordner NIE
# (siehe .gitignore) - nur public_key.pem wird mit auf den Pico uebertragen.
KEYS_DIR = os.path.join(PROJECT_ROOT, "keys")
DEFAULT_PRIVATE_KEY_PATH = os.path.join(KEYS_DIR, "private_key.pem")
DEFAULT_PUBLIC_KEY_PATH = os.path.join(KEYS_DIR, "public_key.pem")
# Bundle-interner Dateiname von public_key.pem - siehe _bundle_source_path()
# (Quelle: KEYS_DIR statt SOURCE_DIR) und _resolve_files_to_bundle() (nur im
# "komplett"-Modus/include_boot_stack enthalten, siehe dort fuer den Grund).
PUBLIC_KEY_BUNDLE_FILENAME = "public_key.pem"
# Dateinamen, die das Geraet in JEDEM per HTTP/GitHub-OTA angewendeten Bundle
# kategorisch blockiert (siehe source/update_manager.py/ota_helpers.py) - ein
# solches Bundle darf daher nie ueber upload_bundle_to_pico() (HTTP) gehen,
# nur seriell (siehe _apply_bundle_entries_via_serial(), das diese Pruefung
# nicht durchlaeuft).
HTTP_OTA_BLOCKED_BUNDLE_FILES = frozenset({"public_key.pem", "license.lic"})
# Lokales Archiv aller ausgestellten Lizenzen (siehe save_license_record()) -
# jede neu signierte license.lic landet hier zusammen mit den beim Ausstellen
# abgefragten Geraete-Werten (Hardware-ID, MicroPython-Version, Port, ...),
# damit spaeter nachvollziehbar ist, welches Geraet wann welche Lizenz bekam.
LICENSES_DIR = os.path.join(PROJECT_ROOT, "lizenzen")
# boot.py/recovery.py werden von MicroPython beim Start direkt ueber ihren
# exakten Dateinamen geladen (nicht per 'import') - dafuer ist ein
# .mpy-Ersatz nicht auf jeder Portversion zuverlaessig gleich behandelt, ein
# Fehlgriff wuerde das Geraet bricken. Beide bleiben daher immer Klartext .py;
# main.py/main_gatehill.py etc. werden dagegen per 'import' geladen, das
# unterstuetzt .mpy transparent.
MPY_EXCLUDED_FILES = {"boot.py", "recovery.py"}


def _mpy_device_name(filename):
    """Liefert den Ziel-Dateinamen auf dem Pico fuer eine Quelldatei: .py ->
    .mpy (Quellcode-Schutz per mpy-cross), ausser MPY_EXCLUDED_FILES (siehe
    oben) - alles andere (html/pak/conf/txt/mission) bleibt unveraendert."""
    if filename.endswith(".py") and filename not in MPY_EXCLUDED_FILES:
        return filename[:-3] + ".mpy"
    return filename


def _expand_with_mpy_variants(names):
    """Ergaenzt eine Namensliste um das jeweilige .py/.mpy-Gegenstueck jeder
    kompilierbaren Datei - noetig, weil Bundles seit der mpy-Kompilierung
    (siehe build_bundle()) .mpy-Namen enthalten, waehrend Aufraeum-/Predelete-
    Listen historisch die rohen .py-Namen fuehren. Ein Geraet, das noch auf
    einer aelteren, unkompilierten Firmware laeuft, muss beim naechsten Update
    sein altes main.py finden UND loeschen koennen, obwohl das neue Bundle nur
    main.mpy enthaelt (sonst wuerden beide Dateien gleichzeitig existieren -
    siehe ota_helpers.py's Gegenstueck-Aufraeumung fuer denselben Grund)."""
    expanded = set(names)
    for name in names:
        if name.endswith(".py") and name not in MPY_EXCLUDED_FILES:
            expanded.add(name[:-3] + ".mpy")
        elif name.endswith(".mpy"):
            expanded.add(name[:-4] + ".py")
    return expanded


# Missionen liegen NICHT in SOURCE_DIR, sondern in ihrem eigenen Ordner (siehe
# mission_builder.py's MISSIONS_DIR) - werden aber trotzdem mit ins normale
# Firmware-Bundle gepackt, damit sie automatisch mit auf den Pico gelangen.
MISSIONS_DIR = os.path.join(PROJECT_ROOT, "missionen")
MISSION_FILE_EXTENSION = ".mission"

BUNDLE_MAGIC = b"FPVBNDL1"
DEFAULT_PICO_URL = "http://192.168.4.1"
# Ziel im MicroPython-Dateisystem (gleiche Ebene wie main.py), kein UF2-Flash.
DEVICE_BUNDLE_PATH = ":firmware.nbo"
DEBUG_ENABLED = True
DEBUG_LOG_FILE = os.path.join(PROJECT_ROOT, "build_firmware_debug.log")

# Versionsnummer (Format X.Y.Z): version.json ist die persistente Quelle der
# Wahrheit im Repo, firmware_version.txt ist die simple Textdatei, die mit ins
# Bundle gepackt wird, damit main.py sie auf dem Pico anzeigen kann. Bei jedem
# Bundle-Build (GUI, Kommandozeile, GitHub Actions) wird die letzte Ziffer
# automatisch um 1 erhoeht (z.B. 1.0.0 -> 1.0.1).
VERSION_STATE_FILE = "version.json"
FIRMWARE_VERSION_FILE = "firmware_version.txt"
DEFAULT_VERSION = "1.0.0"
LANGUAGE_BUNDLE_FILENAME = "lang.pak"
EMERGENCY_BUNDLE_FILENAME = "emergency.nbo"
LANGUAGE_FALLBACK_PACK = "en.pak"

# Bundle-Modi:
# - Mit Boot-Stack: boot/recovery + app/web
# - Ohne Boot-Stack: nur main.py + html/admin Dateien
BOOT_STACK_FILES_TO_BUNDLE = [
    "boot.py",
    "recovery.py",
    "boot_runtime.py",
]

APP_FILES_TO_BUNDLE = [
    "firmware_version.txt",
    "hotspot.conf",
    "en.pak",
    "de.pak",
    "hotspot_common.py",
    "ota_helpers.py",
    "update_manager.py",
    "license_verifier.py",
    "github_ota_helpers.py",
    "idcard_helpers.py",
    "misc_routes_helpers.py",
    "upload_helpers.py",
    "challenge_helpers.py",
    "infection_mode.py",
    "koth_mode.py",
    "race_mode.py",
    "gmr.py",
    "trick_profile_helpers.py",
    "main.py",
    "main_LilyGo.py",
    "main_gatehill.py",
    "role_setup.py",
    "index.html",
    "index_gatehill.html",
    "admin_dashboard.html",
    "admin_update.html",
    "admin_simulate.html",
    "admin_profiles.html",
    "admin_system.html",
    "admin_idcard.html",
    "admin_challenges.html",
    "admin_infection.html",
    "admin_koth.html",
    "admin_race.html",
    "admin_credits.html",
    "challenges_view.html",
    "infection_view.html",
    "gamemodes_view.html",
]

RECOVERY_FILES_TO_BUNDLE = [
    "boot.py",
    "recovery.py",
    "hotspot_common.py",
    "hotspot.conf",
    "boot_runtime.py",
    "ota_helpers.py",
    "update_manager.py",
    "firmware_version.txt",
]

DEFAULT_INCLUDE_BOOT_STACK = False
DEFAULT_BUILD_COMPLETE_FIRMWARE = False
DEFAULT_BUILD_LIGHT_FIRMWARE = False
DEFAULT_BUILD_RECOVERY_FIRMWARE = False
DEFAULT_BUILD_LANGUAGE_PACK = False
DEFAULT_BUILD_BOOT_MAIN_ONLY = False
MANIFEST_FILE = os.path.join(BUILD_DIR, ".last_bundle_manifest.json")


def get_files_to_bundle(include_boot_stack=DEFAULT_INCLUDE_BOOT_STACK):
    files = list(APP_FILES_TO_BUNDLE)
    if include_boot_stack:
        files = list(BOOT_STACK_FILES_TO_BUNDLE) + files
    return files

OPTIONAL_FILES_TO_BUNDLE = []
RECOVERY_MODE_FILES_SET = _expand_with_mpy_variants(RECOVERY_FILES_TO_BUNDLE)


def _bundle_source_path(source_dir, filename):
    """Loest den tatsaechlichen Quellpfad einer Bundle-Datei auf. Mission-
    Dateien (*.mission) liegen im separaten MISSIONS_DIR statt in source_dir,
    public_key.pem im separaten KEYS_DIR (siehe PUBLIC_KEY_BUNDLE_FILENAME) -
    ueberall dort, wo der Bundle-Prozess auf eine Datei zugreifen will
    (Lesen, Groessen-/Vorhanden-Pruefung in der GUI), muss diese Funktion
    statt eines direkten os.path.join(source_dir, filename) verwendet werden."""
    if filename.endswith(MISSION_FILE_EXTENSION):
        return os.path.join(MISSIONS_DIR, filename)
    if filename == PUBLIC_KEY_BUNDLE_FILENAME:
        return DEFAULT_PUBLIC_KEY_PATH
    return os.path.join(source_dir, filename)


def _resolve_mission_files():
    """Findet alle lokal vorhandenen .mission Dateien (missionen/-Ordner) und
    gibt ihre blossen Dateinamen zurueck - analog zu
    _resolve_language_pack_files() fuer .pak Dateien. Missionen sind
    nutzererstellter Inhalt mit beliebigen Namen, daher dynamische Suche statt
    einer festen Liste wie bei APP_FILES_TO_BUNDLE."""
    missions = []
    try:
        for filename in os.listdir(MISSIONS_DIR):
            if not filename.endswith(MISSION_FILE_EXTENSION):
                continue
            full_path = os.path.join(MISSIONS_DIR, filename)
            if os.path.isfile(full_path):
                missions.append(filename)
    except Exception:
        pass
    missions.sort()
    return missions


def _read_bundle_file_bytes(source_dir, filename):
    file_path = _bundle_source_path(source_dir, filename)
    if os.path.isfile(file_path):
        with open(file_path, "rb") as f:
            return f.read()
    return None


def _classify_bundle_mode(bundle_entries):
    names = tuple(bundle_entries or ())
    if not names:
        return "light"
    if all(name.endswith(".pak") for name in names):
        return "language"

    name_set = set(names)
    has_main = "main.py" in name_set or "main.mpy" in name_set
    if has_main and "boot.py" in name_set:
        return "complete"

    if name_set and name_set.issubset(RECOVERY_MODE_FILES_SET):
        return "recovery"

    return "light"


def _build_file_signature_map(source_dir):
    signatures = {}
    tracked = get_files_to_bundle(True) + OPTIONAL_FILES_TO_BUNDLE + _resolve_mission_files()
    for filename in tracked:
        content = _read_bundle_file_bytes(source_dir, filename)
        if content is None:
            signatures[filename] = None
            continue
        signatures[filename] = hashlib.sha256(content).hexdigest()
    return signatures


def _load_manifest():
    try:
        with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _save_manifest(source_dir):
    os.makedirs(BUILD_DIR, exist_ok=True)
    data = _build_file_signature_map(source_dir)
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def _resolve_language_pack_files(source_dir):
    packs = []
    try:
        for filename in os.listdir(source_dir):
            if not filename.endswith(".pak"):
                continue
            if filename == LANGUAGE_FALLBACK_PACK:
                continue
            full_path = os.path.join(source_dir, filename)
            if os.path.isfile(full_path):
                packs.append(filename)
    except Exception:
        pass
    packs.sort()
    return packs


def _order_bundle_files_for_apply(file_list):
    """Sichert Apply-Reihenfolge: main.py/boot.py immer zuletzt.

    Wenn beide enthalten sind, wird main.py direkt vor boot.py einsortiert,
    damit der neue main-Code vor dem neuen Boot-Code auf dem Ziel liegt.
    """
    ordered = [name for name in file_list if name not in ("main.py", "boot.py")]
    if "main.py" in file_list:
        ordered.append("main.py")
    if "boot.py" in file_list:
        ordered.append("boot.py")
    return ordered


def _resolve_files_to_bundle(
    source_dir,
    include_boot_stack,
    light_mode,
    recovery_mode=False,
    language_pack_mode=False,
    boot_main_only_mode=False,
):
    if language_pack_mode:
        return _resolve_language_pack_files(source_dir)

    if boot_main_only_mode:
        return _order_bundle_files_for_apply(["main.py", "boot.py"])

    if recovery_mode:
        base = list(RECOVERY_FILES_TO_BUNDLE)
    else:
        # Missionen sind normaler App-Inhalt und werden bei jedem regulaeren
        # Bundle-Build (komplett oder light) automatisch mit eingesammelt -
        # im Light-Modus greift dieselbe Signatur-Diff-Logik wie fuer alle
        # anderen Dateien (siehe _build_file_signature_map), damit nur
        # tatsaechlich neue/geaenderte Missionen erneut hochgeladen werden.
        base = get_files_to_bundle(include_boot_stack) + _resolve_mission_files()

        # public_key.pem NUR in der kompletten Firmware (--mode complete,
        # include_boot_stack=True) mitliefern - NICHT in normal/light. Grund:
        # die komplette Variante ist ausschliesslich fuer den seriellen Weg
        # gedacht (siehe _apply_bundle_entries_via_serial() sowie
        # build_and_flash_with_license(), die genau das schon so handhaben),
        # der die geraeteseitige Bundle-Pruefung gar nicht durchlaeuft. Die
        # normale/leichte Variante (firmware.nbo) wird dagegen per HTTP-Upload
        # und automatischem GitHub-OTA angewendet - dort blockiert das Geraet
        # public_key.pem in JEDEM Bundle kategorisch (siehe
        # source/update_manager.py/ota_helpers.py), ein Aufnehmen dort wuerde
        # dieses Update komplett zum Abbrechen bringen. Siehe auch die
        # Absicherung in upload_bundle_to_pico() weiter unten.
        if include_boot_stack:
            base = base + [PUBLIC_KEY_BUNDLE_FILENAME]

    # Standard-Workflow: en.pak + de.pak im Haupt-Firmware-Bundle.
    # Weitere Sprachen werden ausschliesslich ueber den lang.pak-Workflow gebaut.
    filtered_base = []
    for filename in base:
        if filename.endswith(".pak") and filename not in ("en.pak", "de.pak"):
            continue
        filtered_base.append(filename)
    base = filtered_base

    optional_present = []
    for filename in OPTIONAL_FILES_TO_BUNDLE:
        if os.path.isfile(os.path.join(source_dir, filename)):
            optional_present.append(filename)

    selected = list(base)
    if light_mode and not recovery_mode:
        previous = _load_manifest()
        current = _build_file_signature_map(source_dir)
        changed = []
        for filename in base:
            now_sig = current.get(filename)
            old_sig = previous.get(filename)
            if now_sig != old_sig:
                changed.append(filename)
        # Light-Firmware soll immer recovery.py enthalten.
        if "recovery.py" not in changed and "recovery.py" in base:
            changed.append("recovery.py")
        selected = changed

    selected.extend(optional_present)
    return _order_bundle_files_for_apply(selected)


def _debug(message):
    if not DEBUG_ENABLED:
        return
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[DEBUG {ts}] {message}"
    print(line)
    try:
        with open(DEBUG_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _shorten(text, max_len=800):
    text = str(text or "")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "...<truncated>"


def _read_version_state(source_dir):
    path = os.path.join(source_dir, VERSION_STATE_FILE)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        version = str(data.get("version", DEFAULT_VERSION)).strip()
        return version or DEFAULT_VERSION
    except Exception:
        return DEFAULT_VERSION


def bump_firmware_version(source_dir):
    """Erhoeht die Patch-Ziffer der Versionsnummer (X.Y.Z -> X.Y.(Z+1)) bei
    jedem Bundle-Build, persistiert sie in version.json (Quelle der Wahrheit
    im Repo) und schreibt den neuen Wert zusaetzlich in firmware_version.txt,
    damit die Firmware selbst (main.py/index.html/admin_system.html) sie
    anzeigen kann. Wird von build_bundle() automatisch aufgerufen - egal ob
    ueber GUI, Kommandozeile oder GitHub Actions gebaut wird."""
    current = _read_version_state(source_dir)
    parts = current.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        parts[2] = str(int(parts[2]) + 1)
    except ValueError:
        parts = ["1", "0", "1"]
    new_version = ".".join(parts[:3])

    version_state_path = os.path.join(source_dir, VERSION_STATE_FILE)
    with open(version_state_path, "w", encoding="utf-8") as f:
        json.dump({"version": new_version}, f)

    version_txt_path = os.path.join(source_dir, FIRMWARE_VERSION_FILE)
    with open(version_txt_path, "w", encoding="utf-8") as f:
        f.write(new_version)

    _debug(f"bump_firmware_version: {current} -> {new_version}")
    return new_version


def build_bundle(
    source_dir,
    output_path,
    progress_callback=None,
    include_boot_stack=DEFAULT_INCLUDE_BOOT_STACK,
    light_mode=DEFAULT_BUILD_LIGHT_FIRMWARE,
    recovery_mode=DEFAULT_BUILD_RECOVERY_FIRMWARE,
    language_pack_mode=DEFAULT_BUILD_LANGUAGE_PACK,
    boot_main_only_mode=DEFAULT_BUILD_BOOT_MAIN_ONLY,
    bump_version=True,
):
    """Baut das Bundle. progress_callback(done, total, filename) wird nach
    jeder verpackten Datei aufgerufen (fuer Fortschrittsanzeigen in der GUI)."""
    _debug(f"build_bundle start: source_dir={source_dir} output_path={output_path}")
    if bump_version:
        new_version = bump_firmware_version(source_dir)
        _debug(f"build_bundle version bumped to {new_version}")
    else:
        new_version = _read_version_state(source_dir)
        _debug(f"build_bundle version kept at {new_version}")
    files_to_bundle = _resolve_files_to_bundle(
        source_dir,
        include_boot_stack,
        light_mode,
        recovery_mode,
        language_pack_mode,
        boot_main_only_mode,
    )
    _debug(
        "build_bundle mode: "
        f"include_boot_stack={include_boot_stack} light_mode={light_mode} recovery_mode={recovery_mode} "
        f"language_pack_mode={language_pack_mode} boot_main_only_mode={boot_main_only_mode} "
        f"files={files_to_bundle}"
    )
    included = []
    missing = []

    # Quellcode-Schutz: .py-Dateien werden (ausser boot.py/recovery.py, siehe
    # MPY_EXCLUDED_FILES) per mpy-cross zu .mpy kompiliert, BEVOR sie ins
    # Bundle wandern - genau wie beim lizenzierten Build (siehe
    # build_and_flash_with_license()/compile_sources_to_mpy()), nur jetzt fuer
    # JEDES firmware.nbo (auch das ueber die CLI/GitHub Actions gebaute
    # Release-Bundle). Ein reines Sprachpaket enthaelt ohnehin keinen Code,
    # daher dort kein mpy-cross noetig/erzwungen.
    needs_mpy = not language_pack_mode and any(
        f.endswith(".py") and f not in MPY_EXCLUDED_FILES for f in files_to_bundle
    )
    mpy_cross_cmd = _resolve_mpy_cross_command() if needs_mpy else None

    bundle_entries = []
    with tempfile.TemporaryDirectory() as tmp_compile_dir:
        for filename in files_to_bundle:
            src_path = _bundle_source_path(source_dir, filename)
            if not os.path.isfile(src_path):
                missing.append(filename)
                continue

            if mpy_cross_cmd and filename.endswith(".py") and filename not in MPY_EXCLUDED_FILES:
                device_name = _mpy_device_name(filename)
                dst_path = os.path.join(tmp_compile_dir, device_name)
                if progress_callback:
                    progress_callback(len(bundle_entries) + 1, len(files_to_bundle), f"Kompiliere {filename} -> {device_name}")
                result = subprocess.run(
                    mpy_cross_cmd + [src_path, "-o", dst_path],
                    capture_output=True, text=True, timeout=60,
                )
                if result.returncode != 0:
                    raise Exception(f"mpy-cross fehlgeschlagen fuer {filename}: {(result.stderr or result.stdout).strip()}")
                with open(dst_path, "rb") as f:
                    content = f.read()
                bundle_entries.append((device_name, content))
            else:
                with open(src_path, "rb") as f:
                    content = f.read()
                bundle_entries.append((filename, content))

    if missing:
        print("WARNUNG: Folgende Dateien fehlen und werden NICHT ins Bundle aufgenommen:")
        for name in missing:
            print(f"  - {name}")
        print()
        _debug(f"build_bundle missing files: {missing}")

    total = len(bundle_entries)

    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with open(output_path, "wb") as out:
        out.write(BUNDLE_MAGIC)
        out.write(struct.pack(">I", total))

        for i, (filename, content) in enumerate(bundle_entries, start=1):
            name_bytes = filename.encode("utf-8")
            out.write(struct.pack(">I", len(name_bytes)))
            out.write(name_bytes)
            out.write(struct.pack(">I", len(content)))
            out.write(content)

            included.append((filename, len(content)))

            if progress_callback:
                progress_callback(i, total, filename)

    _debug(f"build_bundle done: included={len(included)} total_bytes={sum(size for _, size in included)}")
    _save_manifest(source_dir)

    return included, missing


def normalize_base_url(base_url):
    url = (base_url or "").strip()
    if not url:
        url = DEFAULT_PICO_URL
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    normalized = url.rstrip("/")
    _debug(f"normalize_base_url: input={base_url} output={normalized}")
    return normalized


def _post_form_json(url, form_data, timeout=8):
    _debug(f"HTTP POST {url} timeout={timeout} keys={list(form_data.keys())}")
    try:
        encoded = parse.urlencode(form_data).encode("utf-8")
        req = request.Request(url, data=encoded, headers={"Content-Type": "application/x-www-form-urlencoded"})
        with request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
        return json.loads(text)
    except error.HTTPError as e:
        raise Exception(f"HTTP {e.code} bei {url}: {_http_error_detail(e)}") from e
    except error.URLError as e:
        raise Exception(f"Netzwerkfehler bei {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise Exception(f"Ungueltige JSON-Antwort von {url}: {e}") from e


def _http_error_detail(e):
    """Liest den Response-Body eines HTTPError aus und extrahiert - falls
    vorhanden - das JSON-Feld 'error', damit die tatsaechliche Fehlermeldung
    des Pico (statt nur der generischen HTTP-Reason-Phrase wie 'Internal
    Server Error') im Log/der Fehlermeldung landet."""
    try:
        body = e.read().decode("utf-8")
    except Exception:
        return e.reason
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict) and parsed.get("error"):
            return f"{e.reason} - {parsed['error']}"
    except Exception:
        pass
    return f"{e.reason} - {body}" if body else e.reason


def _get_json(url, timeout=12):
    _debug(f"HTTP GET {url} timeout={timeout}")
    try:
        with request.urlopen(url, timeout=timeout) as resp:
            text = resp.read().decode("utf-8")
        return json.loads(text)
    except error.HTTPError as e:
        raise Exception(f"HTTP {e.code} bei {url}: {_http_error_detail(e)}") from e
    except error.URLError as e:
        raise Exception(f"Netzwerkfehler bei {url}: {e.reason}") from e
    except json.JSONDecodeError as e:
        raise Exception(f"Ungueltige JSON-Antwort von {url}: {e}") from e


def upload_bundle_to_pico(bundle_path, base_url, progress_callback=None):
    """Lädt ein bestehendes firmware.nbo Bundle per OTA hoch und finalisiert es."""
    base_url = normalize_base_url(base_url)
    _debug(f"upload_bundle_to_pico start: bundle_path={bundle_path} base_url={base_url}")
    bundle_name = os.path.basename(bundle_path).lower()
    ota_target = LANGUAGE_BUNDLE_FILENAME if bundle_name == LANGUAGE_BUNDLE_FILENAME else "firmware.nbo"
    with open(bundle_path, "rb") as f:
        raw = f.read()
    b64 = base64.b64encode(raw).decode("ascii")
    total_chunks = max(1, (len(b64) + 1023) // 1024)

    for idx in range(total_chunks):
        start = idx * 1024
        end = min(start + 1024, len(b64))
        chunk = b64[start:end]
        response = _post_form_json(
            base_url + "/upload-chunk",
            {
                "index": idx,
                "total": total_chunks,
                "target": ota_target,
                "data": chunk,
            },
        )
        if not response.get("ok"):
            err = response.get("error", "Unbekannter Upload-Fehler")
            raise Exception(f"{err} (Chunk {idx+1}/{total_chunks}, URL: {base_url}/upload-chunk)")
        if progress_callback:
            progress_callback(idx + 1, total_chunks)
        if (idx + 1) % 25 == 0 or (idx + 1) == total_chunks:
            _debug(f"upload_bundle_to_pico chunk progress: {idx + 1}/{total_chunks}")

    # Grosszuegiger Timeout: /finalize-upload dekodiert das komplette Bundle
    # (Base64) und schreibt bei einem Firmware-Bundle alle enthaltenen
    # Dateien (inkl. Backup der jeweils vorherigen Version) einzeln auf das
    # Pico-Flash-Dateisystem - bei 13 Dateien/~160KB kann das auf dem Pico
    # deutlich laenger als 12s dauern, obwohl der Vorgang selbst erfolgreich
    # ist (der alte 12s-Timeout fuehrte hier zu False-Positive "timed out"
    # Fehlern in der GUI, obwohl das Bundle serverseitig korrekt uebernommen
    # wurde).
    finalize = _get_json(base_url + "/finalize-upload", timeout=90)
    if not finalize.get("ok"):
        err = finalize.get("error", "Finalisierung fehlgeschlagen")
        raise Exception(f"{err} (URL: {base_url}/finalize-upload)")
    _debug("upload_bundle_to_pico done")
    return finalize


def _resolve_mpremote_command():
    candidates = []
    seen = set()

    def add_candidate(cmd):
        key = tuple(cmd)
        if key not in seen:
            seen.add(key)
            candidates.append(cmd)

    _debug(f"active python executable: {sys.executable}")

    # Prioritaet 1: Projekt-.venv (falls vorhanden), damit Tooling konsistent
    # im Workspace-Interpreter laeuft statt ueber globale Python-Installationen.
    venv_python = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
    if os.path.isfile(venv_python):
        add_candidate([venv_python, "-m", "mpremote"])

    # Prioritaet 2: der aktuell laufende Interpreter.
    add_candidate([sys.executable, "-m", "mpremote"])

    # Prioritaet 3: standalone mpremote von PATH.
    mpremote_path = shutil.which("mpremote")
    if mpremote_path:
        add_candidate([mpremote_path])

    for base_cmd in candidates:
        _debug(f"mpremote candidate test: {' '.join(base_cmd)}")
        try:
            subprocess.run(
                base_cmd + ["--help"],
                capture_output=True,
                text=True,
                timeout=12,
                check=True,
            )
            _debug(f"mpremote command selected: {' '.join(base_cmd)}")
            return base_cmd
        except Exception:
            _debug(f"mpremote candidate failed: {' '.join(base_cmd)}")
            continue

    raise Exception(
        "mpremote nicht gefunden. Bitte im aktiven Python installieren: "
        f"'{sys.executable} -m pip install mpremote'"
    )


def _cleanup_remote_bundle_artifacts(mpremote_cmd, port, managed_targets=None, remove_targets=None):
    managed = tuple(managed_targets or ())
    remove_now = tuple(remove_targets or ())
    cleanup_script = (
        "import os\n"
        f"MANAGED={repr(managed)}\n"
        f"REMOVE_NOW={repr(remove_now)}\n"
        "for n in ('firmware.nbo','lang.pak','ota_staging.tmp','update.pbp'):\n"
        "  try:\n"
        "    os.remove(n)\n"
        "  except Exception:\n"
        "    pass\n"
        "for name in os.listdir():\n"
        "  remove=False\n"
        "  if name in REMOVE_NOW:\n"
        "    remove=True\n"
        "  elif name.endswith('.txt') and name!='firmware_version.txt':\n"
        "    remove=True\n"
        "  elif name.endswith('.pak'):\n"
        "    remove=True\n"
        "  if name=='main_backup.py' and 'main.py' in MANAGED:\n"
        "    remove=True\n"
        "  elif name.endswith('.bak'):\n"
        "    base=name[:-4]\n"
        "    if base in MANAGED:\n"
        "      remove=True\n"
        "  elif name.endswith('.bndl_tmp'):\n"
        "    base=name[:-9]\n"
        "    if base in MANAGED:\n"
        "      remove=True\n"
        "  if remove:\n"
        "    try:\n"
        "      os.remove(name)\n"
        "    except Exception:\n"
        "      pass\n"
        "print('CLEANUP_OK')"
    )
    try:
        _run_mpremote(mpremote_cmd, ["connect", port, "exec", cleanup_script], timeout=20)
        _debug(f"remote cleanup done on {port}")
    except Exception as e:
        # Cleanup ist best effort; ein Fehlschlag hier soll den Upload nicht
        # sofort abbrechen, kann aber im Debug helfen.
        _debug(f"remote cleanup skipped on {port}: {_shorten(e)}")


# Fehlermuster, die typischerweise transiente USB/Treiber-Haenger sind (kein
# echter Hardwaredefekt) - ein erneuter Versuch loest das meistens.
# "could not enter raw repl"/TransportError tritt v.a. dann auf, wenn der
# Pico gerade seine volle Firmware ausfuehrt (Hardware-Watchdog aktiv, siehe
# boot.py's BOOT_WDT_TIMEOUT_MS) und der Raw-REPL-Handshake laenger dauert
# als der Watchdog erlaubt - der Watchdog resettet das Geraet dann MITTEN im
# Handshake. Ein erneuter Versuch nach kurzer Pause klappt danach meist,
# weil der Pico frisch gebootet und (noch) nicht beschaeftigt ist.
_TRANSIENT_SERIAL_ERROR_MARKERS = (
    "SerialTimeoutException",
    "Write timeout",
    "ClearCommError failed",
    "could not enter raw repl",
    "TransportError",
)


def _run_mpremote(mpremote_cmd, args, timeout=120, retries=0, retry_delay=2.0):
    cmd_text = " ".join(mpremote_cmd + args)
    attempt = 0
    while True:
        attempt += 1
        _debug(f"mpremote run: {cmd_text} timeout={timeout} attempt={attempt}/{retries + 1}")
        try:
            result = subprocess.run(
                mpremote_cmd + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
            )
            _debug(f"mpremote ok: {cmd_text} stdout={_shorten(result.stdout)} stderr={_shorten(result.stderr)}")
            return result
        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or "").strip()
            is_transient = any(marker in err for marker in _TRANSIENT_SERIAL_ERROR_MARKERS)
            if is_transient and attempt <= retries:
                _debug(
                    f"mpremote transient error (attempt {attempt}/{retries + 1}), "
                    f"retrying in {retry_delay}s: {_shorten(err, 300)}"
                )
                time.sleep(retry_delay)
                continue
            _debug(f"mpremote error: {cmd_text} rc={e.returncode} raw={_shorten(err, 2000)}")
            if (
                "ClearCommError failed" in err
                or "serial.serialutil.SerialException" in err
                or "PermissionError(13" in err
            ):
                raise Exception(
                    "Serieller COM-Port ist blockiert oder kein gueltiger Pico-Port. "
                    "Bitte Thonny/Serial-Monitor schliessen, USB kurz neu verbinden und erneut versuchen."
                ) from e

            if "SerialTimeoutException" in err or "Write timeout" in err:
                raise Exception(
                    "Serieller Schreib-Timeout: der Pico hat waehrend der Uebertragung nicht rechtzeitig "
                    "reagiert (meist ein temporaerer USB/Treiber-Haenger, kein Codefehler). "
                    "Tipps: anderes/kuerzeres USB-Kabel verwenden, andere USB-Buchse (moeglichst direkt am "
                    "Mainboard statt Hub), Thonny/Serial-Monitor schliessen, dann erneut versuchen."
                ) from e

            if "No space left on device" in err:
                raise Exception(
                    "Pico-Dateisystem voll: fuer firmware.nbo ist nicht genug Platz frei. "
                    "Tipps: 1) altes Bundle/Temp-Dateien loeschen, 2) Light-Firmware bauen, "
                    "3) unnoetige Dateien auf dem Pico entfernen."
                ) from e

            if err:
                lines = [line for line in err.splitlines() if line.strip()]
                if len(lines) > 10:
                    err = "\n".join(lines[-10:])
            raise Exception(err or f"mpremote Aufruf fehlgeschlagen: {' '.join(args)}") from e
        except subprocess.TimeoutExpired as e:
            if attempt <= retries:
                _debug(f"mpremote timeout (attempt {attempt}/{retries + 1}), retrying in {retry_delay}s")
                time.sleep(retry_delay)
                continue
            raise Exception(f"mpremote Timeout: {' '.join(args)}") from e


def _extract_serial_port_from_line(line):
    patterns = [r"(/dev/tty[^\s,;:]+)", r"(COM\d+)"]
    for pattern in patterns:
        m = re.search(pattern, line, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _list_system_serial_ports():
    if list_ports is None:
        return []
    ports = []
    try:
        for info in list_ports.comports():
            dev = str(getattr(info, "device", "") or "").strip()
            if dev and dev not in ports:
                ports.append(dev)
    except Exception:
        return []
    _debug(f"system serial ports: {ports}")
    return ports


def auto_detect_pico_ports(mpremote_cmd):
    _debug("auto_detect_pico_ports start")
    lines = []
    try:
        listing = _run_mpremote(mpremote_cmd, ["connect", "list"], timeout=15)
        lines = [line.strip() for line in (listing.stdout or "").splitlines() if line.strip()]
    except Exception:
        lines = []

    preferred = []
    fallback = []
    for line in lines:
        port = _extract_serial_port_from_line(line)
        if not port:
            continue
        if port in fallback:
            continue
        fallback.append(port)
        lowered = line.lower()
        if ("2e8a" in lowered) or ("raspberry" in lowered) or ("pico" in lowered):
            preferred.append(port)

    ordered = []
    for port in preferred:
        if port not in ordered:
            ordered.append(port)
    for port in fallback:
        if port not in ordered:
            ordered.append(port)

    # Ergaenze alle System-COM-Ports (z.B. COM11), auch wenn mpremote list
    # sie gerade nicht sauber labelt.
    for port in _list_system_serial_ports():
        if port not in ordered:
            ordered.append(port)

    _debug(f"auto_detect_pico_ports result: {ordered}")

    return ordered


def _read_bundle_entry_names(bundle_path):
    names = []
    with open(bundle_path, "rb") as f:
        magic = f.read(len(BUNDLE_MAGIC))
        if magic != BUNDLE_MAGIC:
            raise Exception("Ungueltiges Bundle (Magic)")

        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            raise Exception("Bundle beschaedigt (count)")
        (count,) = struct.unpack(">I", count_bytes)

        for _ in range(count):
            name_len_bytes = f.read(4)
            if len(name_len_bytes) < 4:
                raise Exception("Bundle beschaedigt (name len)")
            (name_len,) = struct.unpack(">I", name_len_bytes)

            name_bytes = f.read(name_len)
            if len(name_bytes) < name_len:
                raise Exception("Bundle beschaedigt (name)")
            name = name_bytes.decode("utf-8")
            names.append(name)

            content_len_bytes = f.read(4)
            if len(content_len_bytes) < 4:
                raise Exception("Bundle beschaedigt (content len)")
            (content_len,) = struct.unpack(">I", content_len_bytes)
            if content_len > 0:
                f.seek(content_len, os.SEEK_CUR)

    return names


def _iter_bundle_entries(bundle_path):
    with open(bundle_path, "rb") as f:
        magic = f.read(len(BUNDLE_MAGIC))
        if magic != BUNDLE_MAGIC:
            raise Exception("Ungueltiges Bundle (Magic)")

        count_bytes = f.read(4)
        if len(count_bytes) < 4:
            raise Exception("Bundle beschaedigt (count)")
        (count,) = struct.unpack(">I", count_bytes)

        for _ in range(count):
            name_len_bytes = f.read(4)
            if len(name_len_bytes) < 4:
                raise Exception("Bundle beschaedigt (name len)")
            (name_len,) = struct.unpack(">I", name_len_bytes)

            name_bytes = f.read(name_len)
            if len(name_bytes) < name_len:
                raise Exception("Bundle beschaedigt (name)")
            name = name_bytes.decode("utf-8")

            content_len_bytes = f.read(4)
            if len(content_len_bytes) < 4:
                raise Exception("Bundle beschaedigt (content len)")
            (content_len,) = struct.unpack(">I", content_len_bytes)

            content = f.read(content_len)
            if len(content) < content_len:
                raise Exception("Bundle beschaedigt (content)")
            yield name, content


def _is_safe_bundle_entry_filename(filename):
    """Strukturelle Sicherheitspruefung fuer Bundle-Dateinamen (kein Pfad-
    Traversal, keine versteckten Dateien) - Bundles sind bereits vertrauens-
    wuerdige, von diesem Skript zusammengestellte Update-Einheiten, daher
    keine Einzeldatei-Whitelist-Pruefung mehr gegen eine feste Namensliste
    (das wuerde neue Dateien wie Missionen mit beliebigen Namen ausbremsen)."""
    if not filename:
        return False
    if "/" in filename or "\\" in filename or ".." in filename:
        return False
    if filename.startswith("."):
        return False
    return True


def _apply_bundle_entries_via_serial(mpremote_cmd, port, bundle_path, allowed_names, progress_callback=None):
    def _is_repl_transport_error(exc):
        msg = str(exc or "")
        low = msg.lower()
        return (
            "could not enter raw repl" in low
            or "transporterror" in low
            or "serial.serialutil.serialexception" in low
            or "clearcommerror failed" in low
            or "permissionerror(13" in low
        )

    entries = list(_iter_bundle_entries(bundle_path))
    if not entries:
        raise Exception("Bundle enthaelt keine Dateien")

    names = [name for name, _ in entries]
    disallowed = [name for name in names if not _is_safe_bundle_entry_filename(name)]
    if disallowed:
        raise Exception("Datei im Bundle nicht erlaubt: " + disallowed[0])

    _debug(f"serial direct-apply start on {port}: entries={names}")

    # Vor Direkt-Apply das eventuell bereits kopierte Bundle auf dem Pico loeschen.
    _cleanup_remote_bundle_artifacts(mpremote_cmd, port, managed_targets=tuple(allowed_names))

    # Alle Ziel-Dateien in EINEM Raw-REPL-Call entfernen, um maximal Platz
    # zu schaffen und instabile, wiederholte exec-Wechsel pro Datei zu
    # vermeiden ("could not enter raw repl"). Um .py/.mpy-Gegenstuecke
    # erweitert, damit ein Wechsel von/zu kompilierten Dateien keine Karteileiche
    # (z.B. altes main.py neben neuem main.mpy) hinterlaesst.
    delete_names = tuple(_expand_with_mpy_variants(names))
    delete_script = (
        "import os\n"
        f"NAMES={repr(delete_names)}\n"
        "for n in NAMES:\n"
        "  try:\n"
        "    os.remove(n)\n"
        "  except Exception:\n"
        "    pass\n"
        "print('PREDELETE_OK')"
    )
    _run_mpremote(
        mpremote_cmd,
        ["connect", port, "exec", delete_script],
        timeout=30,
    )

    total = len(entries)
    for idx, (name, content) in enumerate(entries, start=1):
        if progress_callback:
            progress_callback(3, 4, f"Direkt-Upload {name} ({idx}/{total})...")

        with tempfile.NamedTemporaryFile("wb", delete=False) as tf:
            tf.write(content)
            host_temp_path = tf.name
        try:
            last_cp_error = None
            cp_ok = False
            for attempt in range(1, 4):
                try:
                    _run_mpremote(
                        mpremote_cmd,
                        ["connect", port, "cp", host_temp_path, ":" + name],
                        timeout=240,
                    )
                    cp_ok = True
                    if attempt > 1:
                        _debug(f"serial direct-apply cp recovered for {name} on attempt {attempt}")
                    break
                except Exception as e:
                    last_cp_error = e
                    if (attempt >= 3) or (not _is_repl_transport_error(e)):
                        break
                    _debug(
                        "serial direct-apply cp transport error; retrying "
                        f"{name} attempt {attempt}/3 on {port}: {_shorten(e)}"
                    )
                    # Session neu synchronisieren, dann erneut cp versuchen.
                    try:
                        _run_mpremote(mpremote_cmd, ["connect", port, "soft-reset"], timeout=20)
                    except Exception as sr_err:
                        _debug(f"serial direct-apply soft-reset retry failed on {port}: {_shorten(sr_err)}")

            if not cp_ok:
                raise Exception(last_cp_error or "Unbekannter cp-Fehler")
        except Exception as e:
            raise Exception(f"Direkt-Upload fehlgeschlagen ({name}): {e}")
        finally:
            try:
                os.remove(host_temp_path)
            except Exception:
                pass

    _debug(f"serial direct-apply done on {port}: entries={names}")
    return names


def _probe_micropython_port(mpremote_cmd, port):
    _debug(f"probe port start: {port}")
    try:
        result = _run_mpremote(
            mpremote_cmd,
            ["connect", port, "exec", "print('PICO_OK')"],
            timeout=8,
            retries=1,
            retry_delay=2.0,
        )
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        ok = "PICO_OK" in combined
        _debug(f"probe port result: {port} ok={ok}")
        return ok
    except Exception:
        _debug(f"probe port failed: {port}")
        return False


# ==================== OFFLINE-LIZENZSYSTEM ====================
# Baut auf denselben mpremote-Helfern wie der normale serielle Bundle-Upload
# oben auf: Hardware-ID lesen, license.lic signieren, Quellcode per mpy-cross
# schuetzen, alles direkt seriell uebertragen. Siehe source/license_verifier.py
# fuer die Pruef-Gegenseite auf dem Pico.

def ensure_keys_dir():
    os.makedirs(KEYS_DIR, exist_ok=True)


def keys_exist():
    return os.path.isfile(DEFAULT_PRIVATE_KEY_PATH) and os.path.isfile(DEFAULT_PUBLIC_KEY_PATH)


def generate_keypair_if_missing():
    if license_generator is None:
        raise Exception("Paket 'cryptography' nicht installiert (siehe requirements/requirements.txt).")
    ensure_keys_dir()
    if keys_exist():
        return False
    license_generator.generate_keypair(DEFAULT_PRIVATE_KEY_PATH, DEFAULT_PUBLIC_KEY_PATH)
    return True


def save_license_record(hardware_id, customer_id, license_content, device_info=None):
    """Legt jede neu ausgestellte Lizenz dauerhaft unter LICENSES_DIR ab:
    die signierte license.lic selbst PLUS eine .json-Datei mit den beim
    Ausstellen abgefragten Geraete-Werten (Hardware-ID, MicroPython-Version
    auf dem Geraet, mpy-cross-Version, seriellem Port, Kunden-ID, Zeitpunkt).
    So bleibt nachvollziehbar, welches Geraet wann welche Lizenz bekam, auch
    wenn das Geraet spaeter nicht mehr erreichbar ist. Liefert (lic_path, json_path)."""
    os.makedirs(LICENSES_DIR, exist_ok=True)

    issued_date = datetime.now().strftime("%Y%m%d")
    safe_hardware_id = "".join(c for c in hardware_id if c.isalnum()) or "unknown"

    # Nur noch Datum statt vollem Zeitstempel: eine erneute Lizenzausstellung
    # fuer dieselbe Hardware-ID am selben Tag ueberschreibt bewusst die
    # vorherige Datei (jede Hardware-ID soll genau eine aktuelle Lizenz haben).
    base_name = f"{safe_hardware_id}_{issued_date}"

    lic_path = os.path.join(LICENSES_DIR, base_name + ".lic")
    with open(lic_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(license_content)

    record = {
        "hardware_id": hardware_id,
        "customer_id": customer_id,
        "issued_at": issued_date,
    }
    record.update(device_info or {})

    json_path = os.path.join(LICENSES_DIR, base_name + ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return lic_path, json_path


def _resolve_mpy_cross_command():
    candidates = []
    seen = set()

    def add(cmd):
        key = tuple(cmd)
        if key not in seen:
            seen.add(key)
            candidates.append(cmd)

    venv_python = os.path.join(PROJECT_ROOT, ".venv", "Scripts", "python.exe")
    if os.path.isfile(venv_python):
        add([venv_python, "-m", "mpy_cross"])
    add([sys.executable, "-m", "mpy_cross"])
    mpy_cross_path = shutil.which("mpy-cross")
    if mpy_cross_path:
        add([mpy_cross_path])

    for cmd in candidates:
        try:
            subprocess.run(cmd + ["--version"], capture_output=True, text=True, timeout=10, check=True)
            return cmd
        except Exception:
            continue

    raise Exception(
        "mpy-cross nicht gefunden. Bitte installieren: "
        f"'{sys.executable} -m pip install mpy-cross'"
    )


def _get_mpy_cross_version(mpy_cross_cmd):
    try:
        result = subprocess.run(mpy_cross_cmd + ["--version"], capture_output=True, text=True, timeout=10, check=True)
        text = (result.stdout or "") + (result.stderr or "")
        m = re.search(r"v(\d+\.\d+(?:\.\d+)?)", text)
        return m.group(1) if m else None
    except Exception:
        return None


def ensure_device_raw_repl_ready(mpremote_cmd, port):
    """Erzwingt einen sauberen, Raw-REPL-bereiten Zustand auf einem bereits
    ausgewaehlten Port, BEVOR weitere exec-/cp-Aufrufe folgen (gleiches
    Muster wie _push_and_unpack_bundle_on_device()). Wichtig, weil der Pico
    normalerweise seine volle Firmware ausfuehrt (Hardware-Watchdog aktiv,
    siehe boot.py) - ohne diesen Soft-Reset kann der erste exec-Aufruf mit
    "could not enter raw repl" fehlschlagen, weil der Watchdog mitten im
    Handshake feuert. Best effort: ein Fehlschlag hier ist nicht fatal, die
    nachfolgenden Aufrufe haben eigene Retries."""
    try:
        _run_mpremote(mpremote_cmd, ["connect", port, "soft-reset"], timeout=20, retries=2, retry_delay=3.0)
    except Exception as e:
        _debug(f"ensure_device_raw_repl_ready soft-reset failed on {port} (best effort): {_shorten(e)}")


def get_device_micropython_version(mpremote_cmd, port):
    try:
        result = _run_mpremote(
            mpremote_cmd,
            ["connect", port, "exec", "import sys; print('.'.join(str(x) for x in sys.implementation.version[:3]))"],
            timeout=10,
            retries=2,
            retry_delay=3.0,
        )
        lines = [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
        return lines[-1] if lines else None
    except Exception:
        return None


def read_hardware_id(mpremote_cmd, port):
    script = "import machine, binascii; print(binascii.hexlify(machine.unique_id()).decode())"
    result = _run_mpremote(mpremote_cmd, ["connect", port, "exec", script], timeout=15, retries=2, retry_delay=3.0)
    lines = [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
    if not lines:
        raise Exception("Konnte Hardware-ID nicht vom Pico lesen (keine Ausgabe)")
    return lines[-1]


def backup_existing_license(mpremote_cmd, port):
    """Liest eine evtl. vorhandene license.lic vom Pico (Clean-Flash-Schutz -
    siehe source/update_manager.py). Liefert None, falls keine vorhanden ist
    oder sie nicht gelesen werden konnte."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".lic") as tf:
        tmp_path = tf.name
    try:
        _run_mpremote(mpremote_cmd, ["connect", port, "cp", ":license.lic", tmp_path], timeout=20, retries=2, retry_delay=3.0)
        with open(tmp_path, "r", encoding="utf-8") as f:
            content = f.read()
        return content if content.strip() else None
    except Exception:
        return None
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def restore_license(mpremote_cmd, port, content):
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".lic", newline="\n", encoding="utf-8") as tf:
        tf.write(content)
        tmp_path = tf.name
    try:
        _run_mpremote(mpremote_cmd, ["connect", port, "cp", tmp_path, ":license.lic"], timeout=20)
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def compile_sources_to_mpy(mpy_cross_cmd, source_dir, file_list, output_dir, progress_callback=None):
    """Kompiliert alle .py-Dateien aus file_list (ausser MPY_EXCLUDED_FILES)
    zu .mpy in output_dir; alle anderen Dateien (.html/.pak/.conf/.txt sowie
    boot.py/recovery.py) werden unveraendert kopiert. Liefert eine Liste von
    (Ziel-Dateiname-auf-dem-Pico, lokaler_Pfad)-Tupeln."""
    os.makedirs(output_dir, exist_ok=True)
    entries = []
    total = len(file_list)

    for i, filename in enumerate(file_list, start=1):
        src_path = os.path.join(source_dir, filename)
        if not os.path.isfile(src_path):
            continue

        if filename.endswith(".py") and filename not in MPY_EXCLUDED_FILES:
            device_name = filename[:-3] + ".mpy"
            dst_path = os.path.join(output_dir, device_name)
            if progress_callback:
                progress_callback(i, total, f"Kompiliere {filename} -> {device_name}")
            result = subprocess.run(
                mpy_cross_cmd + [src_path, "-o", dst_path],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0:
                raise Exception(f"mpy-cross fehlgeschlagen fuer {filename}: {(result.stderr or result.stdout).strip()}")
            entries.append((device_name, dst_path))
        else:
            device_name = filename
            dst_path = os.path.join(output_dir, device_name)
            if progress_callback:
                progress_callback(i, total, f"Kopiere {filename} (unkompiliert)")
            shutil.copyfile(src_path, dst_path)
            entries.append((device_name, dst_path))

    return entries


def build_and_flash_with_license(customer_id, regenerate_license=True, progress_callback=None):
    """Kompletter Ablauf des Haupt-Buttons "Komplette Firmware inkl. Lizenz
    bauen & installieren": Hardware-ID lesen, license.lic signieren, Quellcode
    per mpy-cross schuetzen, alles direkt seriell uebertragen, Soft-Reset.
    progress_callback(done, total, message) fuer die GUI (gleiches Muster wie
    build_bundle()/upload_bundle_via_serial())."""
    if license_generator is None:
        raise Exception("Paket 'cryptography' nicht installiert (siehe requirements/requirements.txt).")

    def progress(step, total, message):
        if progress_callback:
            progress_callback(step, total, message)

    total_steps = 8
    mpremote_cmd = _resolve_mpremote_command()

    progress(1, total_steps, "Suche Pico ueber USB-Seriell...")
    ports = auto_detect_pico_ports(mpremote_cmd)
    if not ports:
        raise Exception("Kein Pico-COM-Port gefunden. Bitte USB neu verbinden und erneut versuchen.")
    port = None
    for p in ports:
        if _probe_micropython_port(mpremote_cmd, p):
            port = p
            break
    if not port:
        port = ports[0]

    # Pico in einen sauberen Raw-REPL-Zustand zwingen, BEVOR die folgenden
    # exec-/cp-Aufrufe laufen - andernfalls kann der erste Aufruf mit
    # "could not enter raw repl" fehlschlagen, wenn der Pico gerade seine
    # volle Firmware ausfuehrt (Hardware-Watchdog aktiv, siehe boot.py).
    progress(1, total_steps, f"Bereite Pico vor ({port})...")
    ensure_device_raw_repl_ready(mpremote_cmd, port)

    progress(2, total_steps, "Pruefe mpy-cross...")
    mpy_cross_cmd = _resolve_mpy_cross_command()
    cross_version = _get_mpy_cross_version(mpy_cross_cmd)

    progress(3, total_steps, "Pruefe MicroPython-Version auf dem Pico...")
    device_version = get_device_micropython_version(mpremote_cmd, port)
    version_warning = None
    if device_version and cross_version and device_version.split(".")[:2] != cross_version.split(".")[:2]:
        version_warning = (
            f"mpy-cross meldet MicroPython {cross_version}, das Geraet laeuft mit {device_version}. "
            "Bei einer Minor-Versions-Abweichung kann eine .mpy-Datei als 'invalid .mpy file' abgelehnt "
            "werden - im Zweifel mpy-cross auf die passende Version anpassen (siehe requirements.txt)."
        )

    progress(4, total_steps, f"Lese Hardware-ID vom Pico ({port})...")
    hardware_id = read_hardware_id(mpremote_cmd, port)

    progress(5, total_steps, "Sichere evtl. vorhandene license.lic (Clean-Flash-Schutz)...")
    backed_up_license = backup_existing_license(mpremote_cmd, port)

    license_content = None
    license_record_path = None
    if regenerate_license:
        progress(6, total_steps, f"Signiere neue license.lic fuer Hardware-ID {hardware_id}...")
        if not keys_exist():
            raise Exception(
                f"Kein RSA-Schluesselpaar unter {KEYS_DIR} gefunden. "
                "Zuerst 'Schluesselpaar erzeugen' ausfuehren (siehe GUI)."
            )
        private_key = license_generator.load_private_key(DEFAULT_PRIVATE_KEY_PATH)
        license_content = license_generator.sign_license(private_key, hardware_id, customer_id)

        lic_path, _json_path = save_license_record(
            hardware_id, customer_id, license_content,
            device_info={
                "port": port,
                "device_micropython_version": device_version,
                "mpy_cross_version": cross_version,
            },
        )
        license_record_path = lic_path
        progress(6, total_steps, f"Lizenz im Archiv abgelegt: {lic_path}")
    else:
        progress(6, total_steps, "Behalte vorhandene Lizenz (keine neue wird erzeugt)...")

    with tempfile.TemporaryDirectory() as tmp_out:
        progress(7, total_steps, "Kompiliere Quellcode (mpy-cross) und uebertrage auf den Pico...")
        file_list = get_files_to_bundle(include_boot_stack=True)

        def compile_progress(i, n, msg):
            progress(7, total_steps, msg)

        compiled_entries = compile_sources_to_mpy(mpy_cross_cmd, SOURCE_DIR, file_list, tmp_out,
                                                   progress_callback=compile_progress)

        bundle_entries = []
        for device_name, local_path in compiled_entries:
            with open(local_path, "rb") as f:
                bundle_entries.append((device_name, f.read()))

        if license_content is not None:
            bundle_entries.append(("license.lic", license_content.encode("utf-8")))
        with open(DEFAULT_PUBLIC_KEY_PATH, "rb") as f:
            bundle_entries.append(("public_key.pem", f.read()))

        # main.py/main.mpy und boot.py werden ABSICHTLICH als letztes installiert:
        # main ist der von boot.py per 'import' geladene Einstiegspunkt, boot.py
        # der von MicroPython direkt beim Start ausgefuehrte. Alle Abhaengigkeiten
        # (ota_helpers.mpy, license_verifier.mpy, HTML-Seiten, ...) sollen bereits
        # vollstaendig auf dem Geraet liegen, BEVOR diese beiden ersetzt werden.
        def _entry_sort_key(entry):
            name = entry[0]
            if name in ("main.py", "main.mpy"):
                return 1
            if name == "boot.py":
                return 2
            return 0

        bundle_entries.sort(key=_entry_sort_key)

        bundle_path = os.path.join(tmp_out, "firmware.nbo")
        with open(bundle_path, "wb") as out:
            out.write(BUNDLE_MAGIC)
            out.write(struct.pack(">I", len(bundle_entries)))
            for name, content in bundle_entries:
                name_bytes = name.encode("utf-8")
                out.write(struct.pack(">I", len(name_bytes)))
                out.write(name_bytes)
                out.write(struct.pack(">I", len(content)))
                out.write(content)

        device_names = [name for name, _ in bundle_entries]

        def serial_progress(_a, _b, msg):
            progress(7, total_steps, msg)

        # Immer zuerst versuchen, firmware.nbo auf den Pico zu kopieren und dort
        # SELBST entpacken zu lassen (Datei-fuer-Datei, sicherer bei einer
        # Unterbrechung) - fällt nur bei zu wenig Speicher auf den direkten
        # PC-seitigen Datei-fuer-Datei-Copy zurueck. Der Soft-Reset am Ende
        # dieser Funktion deckt bereits den Neustart ab, daher hier keinen
        # zusaetzlichen machine.reset() ausloesen.
        _push_and_unpack_bundle_on_device(
            mpremote_cmd, port, bundle_path, device_names, "firmware.nbo",
            predelete_targets=device_names,
            progress_callback=serial_progress,
            trigger_restart_if_needed=False,
        )

    if not regenerate_license and backed_up_license:
        progress(8, total_steps, "Stelle vorherige license.lic wieder her...")
        restore_license(mpremote_cmd, port, backed_up_license)

    progress(8, total_steps, "Soft-Reset...")
    try:
        _run_mpremote(mpremote_cmd, ["connect", port, "soft-reset"], timeout=20, retries=2, retry_delay=3.0)
    except Exception as e:
        # Best effort: Lizenz + Firmware sind zu diesem Zeitpunkt bereits
        # erfolgreich geschrieben (siehe _push_and_unpack_bundle_on_device()
        # oben) - ein fehlgeschlagener Soft-Reset (z.B. Raw-REPL-Kontention
        # kurz nach dem Neuschreiben vieler Dateien) darf das Gesamtergebnis
        # nicht als Fehlschlag melden. Das Geraet startet auch ohne diesen
        # Soft-Reset spaetestens beim naechsten Power-Cycle mit der neuen
        # Firmware.
        _debug(f"finaler Soft-Reset auf {port} fehlgeschlagen (best effort, Lizenz/Firmware bereits geschrieben): {_shorten(e)}")

    return {
        "port": port,
        "hardware_id": hardware_id,
        "license_issued": license_content is not None,
        "license_record_path": license_record_path,
        "version_warning": version_warning,
    }


def _build_device_unpack_script(allowed_names, remote_bundle_filename):
    """Erzeugt das Python-Skript, das per 'mpremote run' AUF DEM PICO
    ausgefuehrt wird, um ein Bundle (firmware.nbo/lang.pak) selbst zu
    entpacken - Datei fuer Datei: alte Version geloescht, dann neue
    geschrieben, eine nach der anderen. Das ist sicherer bei einer
    Unterbrechung mitten in der Uebertragung als ein Massen-Vorab-Loeschen
    ALLER Zieldateien auf einmal (wie es der Direct-Serial-Copy-Fallback via
    _apply_bundle_entries_via_serial() macht)."""
    allowed_tuple_literal = repr(tuple(allowed_names))
    bundle_file_literal = repr(remote_bundle_filename)
    return f"""import os
import struct
import machine

MAGIC = b"FPVBNDL1"
ALLOWED = {allowed_tuple_literal}
BUNDLE_FILE = {bundle_file_literal}


def read_exact(f, n):
    data = bytearray()
    while len(data) < n:
        chunk = f.read(n - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


extracted = []
with open(BUNDLE_FILE, "rb") as f:
    if read_exact(f, len(MAGIC)) != MAGIC:
        raise Exception("Ungueltiges Bundle (Magic)")

    count_bytes = read_exact(f, 4)
    if len(count_bytes) < 4:
        raise Exception("Bundle beschaedigt (count)")
    (count,) = struct.unpack(">I", count_bytes)

    for _ in range(count):
        name_len_bytes = read_exact(f, 4)
        if len(name_len_bytes) < 4:
            raise Exception("Bundle beschaedigt (name len)")
        (name_len,) = struct.unpack(">I", name_len_bytes)

        name_bytes = read_exact(f, name_len)
        if len(name_bytes) < name_len:
            raise Exception("Bundle beschaedigt (name)")
        name = name_bytes.decode("utf-8")

        content_len_bytes = read_exact(f, 4)
        if len(content_len_bytes) < 4:
            raise Exception("Bundle beschaedigt (content len)")
        (content_len,) = struct.unpack(">I", content_len_bytes)

        if not name or "/" in name or "\\\\" in name or ".." in name or name.startswith("."):
            raise Exception("Datei im Bundle nicht erlaubt: " + name)

        # Low-space-Modus: direkt in die Zieldatei schreiben.
        try:
            os.remove(name + ".bndl_tmp")
        except Exception:
            pass
        # Wichtig bei wenig Flash: alte Zieldatei vor dem Schreiben entfernen,
        # damit nicht parallel alter+neuer Inhalt Platz belegen.
        try:
            os.remove(name)
        except Exception:
            pass
        # Stale Gegenstueck aus einer frueheren Firmware-Generation entfernen
        # (main.py <-> main.mpy) - sonst koennten beide gleichzeitig
        # existieren und MicroPythons Import-Reihenfolge waere ungewiss.
        if name.endswith(".mpy"):
            try:
                os.remove(name[:-4] + ".py")
            except Exception:
                pass
        elif name.endswith(".py"):
            try:
                os.remove(name[:-3] + ".mpy")
            except Exception:
                pass

        remaining = content_len
        try:
            with open(name, "wb") as out:
                while remaining > 0:
                    chunk = f.read(min(512, remaining))
                    if not chunk:
                        raise Exception("Bundle beschaedigt (content)")
                    out.write(chunk)
                    remaining -= len(chunk)
        except OSError as e:
            raise Exception("Zu wenig Speicher beim Schreiben von " + name + ": " + str(e))
        extracted.append(name)

try:
    os.remove(BUNDLE_FILE)
except Exception:
    pass

# Nur Update-Artefakte entfernen, keine Benutzerdaten.
for fixed_name in ("update.pbp", "ota_staging.tmp"):
    try:
        os.remove(fixed_name)
    except Exception:
        pass

for name in os.listdir():
    remove = False
    if name == "main_backup.py" and "main.py" in ALLOWED:
        remove = True
    elif name.endswith(".bak"):
        base = name[:-4]
        if base in ALLOWED:
            remove = True
    elif name.endswith(".bndl_tmp"):
        base = name[:-9]
        if base in ALLOWED:
            remove = True
    if remove:
        try:
            os.remove(name)
        except Exception:
            pass

needs_restart = ("main.py" in extracted) or ("main.mpy" in extracted)
print("SERIAL_APPLY_OK:" + ",".join(extracted))
print("SERIAL_NEEDS_RESTART:" + ("1" if needs_restart else "0"))
"""


def _push_and_unpack_bundle_on_device(
    mpremote_cmd, port, bundle_path, managed_targets, remote_bundle_filename,
    predelete_targets=None, bundle_already_copied=False, allow_low_space_fallback=True,
    progress_callback=None, trigger_restart_if_needed=True,
):
    """Kopiert bundle_path als remote_bundle_filename auf einen BEREITS
    ausgewaehlten Pico-Port und laesst den Pico das Bundle SELBST entpacken
    (siehe _build_device_unpack_script()) - immer der primaer versuchte Weg,
    ein Bundle zu installieren. Faellt bei 'zu wenig Speicher' automatisch auf
    _apply_bundle_entries_via_serial() (Direct-Serial-Copy, PC-seitig Datei
    fuer Datei per 'cp') zurueck, ausser allow_low_space_fallback=False.
    Liefert (extracted_names, needs_restart)."""
    if predelete_targets is None:
        predelete_targets = managed_targets

    remote_device_bundle_path = ":" + remote_bundle_filename
    direct_apply_done = False
    direct_apply_names = []

    if not bundle_already_copied:
        try:
            _cleanup_remote_bundle_artifacts(
                mpremote_cmd, port, managed_targets=managed_targets, remove_targets=predelete_targets,
            )
            _run_mpremote(
                mpremote_cmd, ["connect", port, "cp", bundle_path, remote_device_bundle_path],
                timeout=240, retries=2,
            )
            _debug(f"bundle copied to {port} at {remote_device_bundle_path}")
        except Exception as e:
            msg = str(e)
            low = msg.lower()
            no_space = ("pico-dateisystem voll" in low) or ("no space left on device" in low)
            if no_space and allow_low_space_fallback:
                _debug(f"bundle copy hit no-space; switching to direct serial apply on {port}: {_shorten(msg)}")
                direct_apply_names = _apply_bundle_entries_via_serial(
                    mpremote_cmd, port, bundle_path, managed_targets, progress_callback=progress_callback,
                )
                direct_apply_done = True
            else:
                raise Exception(f"Transfer auf {port} fehlgeschlagen: {e}")

    if progress_callback:
        if direct_apply_done:
            progress_callback(2, 4, "Direkt-Upload ohne Bundle-Datei aktiv")
        else:
            progress_callback(2, 4, "Bundle seriell uebertragen")

    extracted_names = direct_apply_names
    needs_restart = False

    if direct_apply_done:
        needs_restart = ("main.py" in direct_apply_names) or ("main.mpy" in direct_apply_names)
    else:
        unpack_script = _build_device_unpack_script(managed_targets, remote_bundle_filename)
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
            tf.write(unpack_script)
            temp_script_path = tf.name
        try:
            if progress_callback:
                progress_callback(3, 4, "Bundle auf Pico entpacken")

            try:
                _run_mpremote(mpremote_cmd, ["connect", port, "soft-reset"], timeout=20)
            except Exception as sr_err:
                _debug(f"soft-reset vor unpack auf {port} fehlgeschlagen: {_shorten(sr_err)}")

            try:
                run_result = _run_mpremote(
                    mpremote_cmd, ["connect", port, "run", temp_script_path], timeout=240,
                )
                run_output = (run_result.stdout or "") + "\n" + (run_result.stderr or "")
                if "SERIAL_APPLY_OK:" not in run_output:
                    raise Exception("Entpack-Skript beendet ohne Erfolgsmarker SERIAL_APPLY_OK")
                needs_restart = "SERIAL_NEEDS_RESTART:1" in run_output
                marker_line = next(line for line in run_output.splitlines() if line.startswith("SERIAL_APPLY_OK:"))
                extracted_names = [n for n in marker_line[len("SERIAL_APPLY_OK:"):].split(",") if n]
                _debug(f"unpack succeeded on {port}")
            except Exception as e:
                unpack_error = str(e)
                _debug(f"unpack failed on {port}: {_shorten(unpack_error)}")
                low_space_error = "Zu wenig Speicher beim Schreiben von" in unpack_error
                if low_space_error and allow_low_space_fallback:
                    _debug(
                        "unpack low-space detected; trying serial direct-apply fallback "
                        f"on {port}: {_shorten(unpack_error)}"
                    )
                    extracted_names = _apply_bundle_entries_via_serial(
                        mpremote_cmd, port, bundle_path, managed_targets, progress_callback=progress_callback,
                    )
                    needs_restart = "main.py" in extracted_names
                else:
                    raise Exception(f"Entpacken auf {port} fehlgeschlagen: {unpack_error}")
        finally:
            try:
                os.remove(temp_script_path)
            except Exception:
                pass

    if needs_restart and trigger_restart_if_needed:
        _debug(f"triggering post-unpack reset on {port}")
        try:
            _run_mpremote(mpremote_cmd, ["connect", port, "exec", "import machine; machine.reset()"], timeout=20)
        except Exception as reset_err:
            # Reset trennt die serielle Session oft sofort (expected).
            _debug(
                f"post-unpack reset disconnect on {port} is expected; "
                f"ignoring transport error: {_shorten(reset_err)}"
            )

    if progress_callback:
        progress_callback(4, 4, "Bundle auf Pico entpackt")

    return extracted_names, needs_restart


def upload_bundle_via_serial(bundle_path, progress_callback=None):
    """Laedt firmware.nbo per USB-Seriell auf den Pico und entpackt direkt."""
    _debug(f"upload_bundle_via_serial start: bundle_path={bundle_path}")
    mpremote_cmd = _resolve_mpremote_command()

    bundle_name = os.path.basename(bundle_path).lower()
    bundle_entries = _read_bundle_entry_names(bundle_path)
    bundle_mode = _classify_bundle_mode(bundle_entries)
    entries_are_only_language_paks = bool(bundle_entries) and all(name.endswith(".pak") for name in bundle_entries)

    # Modus aus Inhalt ableiten, nicht nur aus Dateiname.
    language_pack_mode = entries_are_only_language_paks

    # Expliziter Schutz: falsch gebautes Bundle mit lang.pak-Namen frueh stoppen.
    if bundle_name == LANGUAGE_BUNDLE_FILENAME and not entries_are_only_language_paks:
        raise Exception(
            "lang.pak enthaelt keine reinen Sprachpaket-Dateien. "
            "Bitte mit 'Builde Sprachpaket' neu erstellen und erneut hochladen."
        )

    remote_bundle_filename = LANGUAGE_BUNDLE_FILENAME if language_pack_mode else "firmware.nbo"
    remote_device_bundle_path = ":" + remote_bundle_filename
    _debug(
        "serial bundle inspection: "
        f"entries={len(bundle_entries)} bundle_mode={bundle_mode} language_pack_mode={language_pack_mode} "
        f"remote_name={remote_bundle_filename}"
    )

    ports = auto_detect_pico_ports(mpremote_cmd)
    if not ports:
        raise Exception(
            "Kein Pico-COM-Port gefunden. "
            "Bitte Thonny/Serial-Monitor schliessen, USB neu verbinden und erneut versuchen."
        )

    selected_port = None
    bundle_already_copied = False
    last_error = ""
    if language_pack_mode:
        managed_targets = tuple(_resolve_language_pack_files(SOURCE_DIR))
    else:
        # Um .py- UND .mpy-Namen erweitert: das Bundle enthaelt seit der
        # mpy-Kompilierung (siehe build_bundle()) .mpy-Namen, waehrend ein
        # Geraet mit einer aelteren, unkompilierten Firmware noch die rohen
        # .py-Dateien traegt - beide Varianten muessen als "verwaltet" gelten,
        # damit predelete_targets unten sie zuverlaessig erfasst.
        managed_targets = tuple(_expand_with_mpy_variants(get_files_to_bundle(True) + OPTIONAL_FILES_TO_BUNDLE))

    if bundle_mode == "complete":
        predelete_targets = tuple(name for name in managed_targets if name != "copil")
    elif bundle_mode in ("light", "recovery"):
        predelete_targets = tuple(name for name in bundle_entries if name in managed_targets and name != "copil")
    else:
        predelete_targets = tuple(name for name in bundle_entries if name in managed_targets and name != "copil")

    _debug(
        "serial managed targets: "
        f"language_pack_mode={language_pack_mode} bundle_mode={bundle_mode} "
        f"targets={managed_targets} predelete={predelete_targets}"
    )

    # Schneller Port-Test: auf jedem Port nur kurzer exec-Probe statt kompletter
    # Datei-Transfer. Das ist deutlich schneller als cp auf jedem COM-Port.
    for idx, port in enumerate(ports, start=1):
        if progress_callback:
            progress_callback(1, 4, f"Pruefe Pico-Port {port} ({idx}/{len(ports)})...")
        if _probe_micropython_port(mpremote_cmd, port):
            selected_port = port
            _debug(f"probe selected port: {port}")
            break

    # Fallback: wenn Probe nicht greift, nacheinander kopieren.
    if not selected_port:
        for idx, port in enumerate(ports, start=1):
            if progress_callback:
                progress_callback(1, 4, f"Fallback-Transfer auf {port} ({idx}/{len(ports)})...")
            try:
                _cleanup_remote_bundle_artifacts(
                    mpremote_cmd,
                    port,
                    managed_targets=managed_targets,
                    remove_targets=predelete_targets,
                )
                _run_mpremote(
                    mpremote_cmd,
                    ["connect", port, "cp", bundle_path, remote_device_bundle_path],
                    timeout=240,
                    retries=2,
                )
                selected_port = port
                bundle_already_copied = True
                _debug(f"fallback selected port via cp: {port}")
                break
            except Exception as e:
                last_error = str(e)
                _debug(f"fallback cp failed on {port}: {_shorten(last_error)}")

    if not selected_port:
        raise Exception(
            f"Konnte {remote_bundle_filename} auf keinem gefundenen COM-Port uebertragen. "
            f"Getestete Ports: {', '.join(ports)}. Letzter Fehler: {last_error}"
        )

    port = selected_port
    if progress_callback:
        progress_callback(1, 4, f"Pico gefunden: {port}")

    _extracted_names, _needs_restart = _push_and_unpack_bundle_on_device(
        mpremote_cmd, port, bundle_path, managed_targets, remote_bundle_filename,
        predelete_targets=predelete_targets,
        bundle_already_copied=bundle_already_copied,
        allow_low_space_fallback=not language_pack_mode,
        progress_callback=progress_callback,
    )

    _debug(f"upload_bundle_via_serial done: port={port}")
    return {
        "ok": True,
        "message": (
            f"Serieller Dateisystem-Upload abgeschlossen (Pico: {port}, Ziel: {remote_device_bundle_path}) "
            "und auf dem Pico entpackt."
        ),
    }


def run_cli(
    output_path=None,
    include_boot_stack=DEFAULT_INCLUDE_BOOT_STACK,
    light_mode=False,
    recovery_mode=False,
    language_pack_mode=False,
    boot_main_only_mode=False,
    bump_version=True,
):
    source_dir = SOURCE_DIR
    output_path = output_path or os.path.join(BUILD_DIR, "firmware.nbo")

    def report(done, total, filename):
        print(f"[{done}/{total}] {filename}")

    included, missing = build_bundle(
        source_dir,
        output_path,
        progress_callback=report,
        include_boot_stack=include_boot_stack,
        light_mode=light_mode,
        recovery_mode=recovery_mode,
        language_pack_mode=language_pack_mode,
        boot_main_only_mode=boot_main_only_mode,
        bump_version=bump_version,
    )

    total_size = sum(size for _, size in included)
    bundle_size = os.path.getsize(output_path)
    current_version = _read_version_state(source_dir)

    print()
    print(f"Firmware-Bundle erstellt: {output_path}")
    print(f"Firmware-Version: {current_version}")
    print()
    print(f"{'Datei':<28} {'Groesse':>10}")
    print("-" * 40)
    for filename, size in included:
        print(f"{filename:<28} {size:>8} B")
    print("-" * 40)
    print(f"{'Summe (Inhalte)':<28} {total_size:>8} B")
    print(f"{'Bundle-Datei gesamt':<28} {bundle_size:>8} B")

    if missing:
        print()
        print(f"HINWEIS: {len(missing)} Datei(en) fehlten und wurden uebersprungen (siehe oben).")

    print()
    print("Naechster Schritt: firmware.nbo im Admin-Bereich unter /admin-update hochladen.")


def launch_gui():
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    source_dir = SOURCE_DIR
    default_output_path = os.path.join(BUILD_DIR, "firmware.nbo")

    root = tk.Tk()
    root.title("FPV Gamification Pico - Firmware Bundle Builder")
    root.geometry("980x760")
    root.resizable(False, False)

    frame = ttk.Frame(root, padding=12)
    frame.pack(fill="both", expand=True)

    ttk.Label(frame, text="Gefundene Firmware-Dateien:", font=("Segoe UI", 10, "bold")).pack(anchor="w")

    tree = ttk.Treeview(frame, columns=("status", "size"), show="tree headings", height=8)
    tree.heading("#0", text="Datei")
    tree.heading("status", text="Status")
    tree.heading("size", text="Groesse")
    tree.column("#0", width=260)
    tree.column("status", width=110, anchor="center")
    tree.column("size", width=110, anchor="e")
    tree.pack(fill="x", pady=(4, 10))
    tree.tag_configure("ok", foreground="#1a7a3c")
    tree.tag_configure("missing", foreground="#b03030")

    include_boot_stack_var = tk.BooleanVar(value=DEFAULT_BUILD_COMPLETE_FIRMWARE)
    build_light_var = tk.BooleanVar(value=DEFAULT_BUILD_LIGHT_FIRMWARE)
    build_recovery_var = tk.BooleanVar(value=DEFAULT_BUILD_RECOVERY_FIRMWARE)
    build_language_pack_var = tk.BooleanVar(value=DEFAULT_BUILD_LANGUAGE_PACK)
    build_boot_main_only_var = tk.BooleanVar(value=DEFAULT_BUILD_BOOT_MAIN_ONLY)

    def on_mode_change():
        if build_language_pack_var.get():
            include_boot_stack_var.set(False)
            build_light_var.set(False)
            build_recovery_var.set(False)
            build_boot_main_only_var.set(False)
            output_var.set(os.path.join(BUILD_DIR, LANGUAGE_BUNDLE_FILENAME))
        elif build_boot_main_only_var.get():
            include_boot_stack_var.set(False)
            build_light_var.set(False)
            build_recovery_var.set(False)
            build_language_pack_var.set(False)
            output_var.set(os.path.join(BUILD_DIR, EMERGENCY_BUNDLE_FILENAME))
        elif build_recovery_var.get():
            build_language_pack_var.set(False)
            build_boot_main_only_var.set(False)
        elif include_boot_stack_var.get() or build_light_var.get():
            build_boot_main_only_var.set(False)
        else:
            output_name = os.path.basename(output_var.get().strip()).lower()
            if output_name in (LANGUAGE_BUNDLE_FILENAME, EMERGENCY_BUNDLE_FILENAME):
                output_var.set(default_output_path)
        scan_files()

    def scan_files():
        _debug("GUI scan_files triggered")
        tree.delete(*tree.get_children())
        selected = _resolve_files_to_bundle(
            source_dir,
            include_boot_stack=include_boot_stack_var.get(),
            light_mode=build_light_var.get(),
            recovery_mode=build_recovery_var.get(),
            language_pack_mode=build_language_pack_var.get(),
            boot_main_only_mode=build_boot_main_only_var.get(),
        )
        scan_dir = source_dir
        for filename in selected:
            file_path = _bundle_source_path(scan_dir, filename)
            if os.path.isfile(file_path):
                size = os.path.getsize(file_path)
                tree.insert("", "end", text=filename, values=("Gefunden", f"{size} B"), tags=("ok",))
            else:
                tree.insert("", "end", text=filename, values=("Fehlt", "-"), tags=("missing",))

        if not selected:
            tree.insert("", "end", text="(keine geaenderten Dateien)", values=("Hinweis", "-"), tags=("missing",))

    scan_files()

    mode_frame = ttk.Frame(frame)
    mode_frame.pack(fill="x", pady=(0, 10))
    ttk.Checkbutton(
        mode_frame,
        text="Builde Komplette Firmware",
        variable=include_boot_stack_var,
        command=on_mode_change,
    ).pack(anchor="w")
    ttk.Checkbutton(
        mode_frame,
        text="Builde Ligth Firmware",
        variable=build_light_var,
        command=on_mode_change,
    ).pack(anchor="w")
    ttk.Checkbutton(
        mode_frame,
        text="Builde Recovery",
        variable=build_recovery_var,
        command=on_mode_change,
    ).pack(anchor="w")
    ttk.Checkbutton(
        mode_frame,
        text="Builde Sprachpaket (lang.pak ohne en.pak)",
        variable=build_language_pack_var,
        command=on_mode_change,
    ).pack(anchor="w")
    ttk.Checkbutton(
        mode_frame,
        text="Builde Emergency (emergency.nbo: nur main.py + boot.py)",
        variable=build_boot_main_only_var,
        command=on_mode_change,
    ).pack(anchor="w")

    path_frame = ttk.Frame(frame)
    path_frame.pack(fill="x", pady=(0, 10))
    ttk.Label(path_frame, text="Ausgabe:").pack(side="left")
    output_var = tk.StringVar(value=default_output_path)
    ttk.Entry(path_frame, textvariable=output_var).pack(side="left", fill="x", expand=True, padx=6)

    def browse_output():
        path = filedialog.asksaveasfilename(
            initialdir=BUILD_DIR,
            initialfile="firmware.nbo",
            defaultextension=".nbo",
            filetypes=[("Firmware Bundle", "*.nbo"), ("Alle Dateien", "*.*")],
        )
        if path:
            output_var.set(path)

    ttk.Button(path_frame, text="Durchsuchen...", command=browse_output).pack(side="left")

    target_frame = ttk.Frame(frame)
    target_frame.pack(fill="x", pady=(0, 10))
    ttk.Label(target_frame, text="Pico URL:").pack(side="left")
    pico_url_var = tk.StringVar(value=DEFAULT_PICO_URL)
    ttk.Entry(target_frame, textvariable=pico_url_var).pack(side="left", fill="x", expand=True, padx=6)

    ttk.Separator(frame, orient="horizontal").pack(fill="x", pady=(0, 10))
    ttk.Label(frame, text="Offline-Lizenz (RSA-Hardware-Kopplung)", font=("Segoe UI", 10, "bold")).pack(anchor="w")

    license_frame = ttk.Frame(frame)
    license_frame.pack(fill="x", pady=(4, 4))
    ttk.Label(license_frame, text="Kunden-ID / Referenz:").pack(side="left")
    customer_id_var = tk.StringVar(value="")
    ttk.Entry(license_frame, textvariable=customer_id_var).pack(side="left", fill="x", expand=True, padx=6)

    keys_status_var = tk.StringVar(value="")

    def update_keys_status():
        if keys_exist():
            keys_status_var.set(f"Schluesselpaar vorhanden ({KEYS_DIR})")
            # Button ausblenden, sobald ein Schluesselpaar existiert - verhindert
            # versehentliches Ueberschreiben per Klick (macht sonst ALLE bisher
            # ausgestellten Lizenzen ungueltig, siehe Warnung in on_generate_keys()).
            generate_keys_button.pack_forget()
        else:
            keys_status_var.set("KEIN Schluesselpaar gefunden - zuerst erzeugen!")
            generate_keys_button.pack(side="right")

    def on_generate_keys():
        if keys_exist():
            if not messagebox.askyesno(
                "Ueberschreiben?",
                "Es existiert bereits ein Schluesselpaar. Ein neues zu erzeugen macht ALLE bisher "
                "ausgestellten Lizenzen ungueltig (der Public Key auf bereits geflashten Geraeten passt "
                "dann nicht mehr). Wirklich ein neues Schluesselpaar erzeugen?",
            ):
                return
        try:
            ensure_keys_dir()
            license_generator.generate_keypair(DEFAULT_PRIVATE_KEY_PATH, DEFAULT_PUBLIC_KEY_PATH)
        except Exception as e:
            messagebox.showerror("Fehler", str(e))
            return
        update_keys_status()
        messagebox.showinfo("Fertig", f"Neues Schluesselpaar erzeugt unter {KEYS_DIR}.")

    keys_frame = ttk.Frame(frame)
    keys_frame.pack(fill="x", pady=(0, 4))
    ttk.Label(keys_frame, textvariable=keys_status_var).pack(side="left")
    generate_keys_button = ttk.Button(keys_frame, text="Schluesselpaar erzeugen", command=on_generate_keys)
    update_keys_status()

    regenerate_license_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(
        frame,
        text="Neue Lizenz fuer dieses Geraet erzeugen (aus, um eine vorhandene Lizenz zu behalten)",
        variable=regenerate_license_var,
    ).pack(anchor="w", pady=(0, 10))

    progress_var = tk.DoubleVar(value=0)
    ttk.Progressbar(frame, variable=progress_var, maximum=100).pack(fill="x", pady=(0, 6))

    status_var = tk.StringVar(value="Bereit.")
    ttk.Label(frame, textvariable=status_var, wraplength=640, justify="left").pack(anchor="w", pady=(0, 10))

    btn_frame = ttk.Frame(frame)
    btn_frame.pack(fill="x")
    build_button = ttk.Button(btn_frame, text="Bundle erstellen")
    build_button.pack(side="left")
    upload_button = ttk.Button(btn_frame, text="Bundle hochladen + entpacken")
    upload_button.pack(side="left", padx=6)
    lang_upload_button = ttk.Button(btn_frame, text="lang.pak hochladen")
    lang_upload_button.pack(side="left", padx=6)
    lang_serial_upload_button = ttk.Button(btn_frame, text="lang.pak seriell uebertragen")
    lang_serial_upload_button.pack(side="left", padx=6)
    serial_upload_button = ttk.Button(btn_frame, text="Seriell ins Dateisystem + entpacken (Auto)")
    serial_upload_button.pack(side="left", padx=6)
    ttk.Button(btn_frame, text="Aktualisieren", command=scan_files).pack(side="left", padx=6)

    license_build_button = ttk.Button(
        frame,
        text="Komplette Firmware inkl. Lizenz bauen & installieren (seriell)",
    )
    license_build_button.pack(fill="x", pady=(8, 0))

    def build_worker(output_path):
        output_name = os.path.basename(output_path).lower()
        output_is_lang_bundle = output_name == LANGUAGE_BUNDLE_FILENAME
        include_boot_stack = include_boot_stack_var.get()
        light_mode = build_light_var.get()
        recovery_mode = build_recovery_var.get()
        language_pack_mode = build_language_pack_var.get()
        boot_main_only_mode = build_boot_main_only_var.get()

        # Schutz gegen Fehlbedienung: wenn der Ausgabename lang.pak ist,
        # muss auch wirklich Sprachpaket-Modus aktiv sein.
        if output_is_lang_bundle and not language_pack_mode:
            language_pack_mode = True

        if boot_main_only_mode:
            include_boot_stack = False
            light_mode = False
            recovery_mode = False
            language_pack_mode = False
        if recovery_mode:
            include_boot_stack = False
            light_mode = False
            language_pack_mode = False
            boot_main_only_mode = False
        if language_pack_mode:
            include_boot_stack = False
            light_mode = False
            recovery_mode = False
            boot_main_only_mode = False
        _debug(
            "GUI build_worker start: "
            f"output_path={output_path} include_boot_stack={include_boot_stack} light_mode={light_mode} "
            f"recovery_mode={recovery_mode} language_pack_mode={language_pack_mode} "
            f"boot_main_only_mode={boot_main_only_mode}"
        )
        def report(done, total, filename):
            def update():
                progress_var.set(done / total * 100 if total else 100)
                status_var.set(f"Verpacke {filename} ({done}/{total})...")
            root.after(0, update)

        try:
            included, missing = build_bundle(
                source_dir,
                output_path,
                progress_callback=report,
                include_boot_stack=include_boot_stack,
                light_mode=light_mode,
                recovery_mode=recovery_mode,
                language_pack_mode=language_pack_mode,
                boot_main_only_mode=boot_main_only_mode,
            )
            total_size = sum(size for _, size in included)
            bundle_size = os.path.getsize(output_path)
            current_version = _read_version_state(source_dir)
            total_size_kb = total_size / 1024.0
            bundle_size_kb = bundle_size / 1024.0

            def finish():
                progress_var.set(100)
                msg = (
                    f"Fertig: {output_path}\n"
                    f"Firmware-Version: {current_version}\n"
                    f"{len(included)} Datei(en), {total_size} B ({total_size_kb:.1f} KB) Inhalt, "
                    f"{bundle_size} B ({bundle_size_kb:.1f} KB) Bundle."
                )
                if missing:
                    msg += f"\nFehlend (uebersprungen): {', '.join(missing)}"
                status_var.set(msg)
                build_button.config(state="normal")
                license_build_button.config(state="normal")
                upload_button.config(state="normal")
                lang_upload_button.config(state="normal")
                lang_serial_upload_button.config(state="normal")
                serial_upload_button.config(state="normal")
                messagebox.showinfo("Bundle erstellt", msg)

            root.after(0, finish)
        except Exception as e:
            err_text = str(e)
            _debug(f"GUI build_worker failed: {_shorten(err_text)}")

            def fail():
                status_var.set(f"Fehler: {err_text}")
                build_button.config(state="normal")
                license_build_button.config(state="normal")
                upload_button.config(state="normal")
                lang_upload_button.config(state="normal")
                lang_serial_upload_button.config(state="normal")
                serial_upload_button.config(state="normal")
                messagebox.showerror("Fehler", err_text)

            root.after(0, fail)

    def start_build():
        output_path = output_var.get().strip()
        _debug(f"GUI start_build called: output_path={output_path}")
        if not output_path:
            messagebox.showerror("Fehler", "Bitte einen Ausgabepfad angeben.")
            return

        output_name = os.path.basename(output_path).lower()
        effective_language_pack_mode = build_language_pack_var.get() or (output_name == LANGUAGE_BUNDLE_FILENAME)
        effective_boot_main_only_mode = False if effective_language_pack_mode else build_boot_main_only_var.get()
        effective_recovery_mode = False if (effective_language_pack_mode or effective_boot_main_only_mode) else build_recovery_var.get()
        effective_include_boot_stack = False if (effective_recovery_mode or effective_language_pack_mode or effective_boot_main_only_mode) else include_boot_stack_var.get()
        effective_light_mode = False if (effective_recovery_mode or effective_language_pack_mode or effective_boot_main_only_mode) else build_light_var.get()

        active_files = _resolve_files_to_bundle(
            source_dir,
            include_boot_stack=effective_include_boot_stack,
            light_mode=effective_light_mode,
            recovery_mode=effective_recovery_mode,
            language_pack_mode=effective_language_pack_mode,
            boot_main_only_mode=effective_boot_main_only_mode,
        )
        present = [f for f in active_files if os.path.isfile(_bundle_source_path(source_dir, f))]
        if not present:
            messagebox.showerror("Fehler", "Keine der erwarteten Firmware-Dateien gefunden.")
            return
        build_button.config(state="disabled")
        license_build_button.config(state="disabled")
        upload_button.config(state="disabled")
        lang_upload_button.config(state="disabled")
        lang_serial_upload_button.config(state="disabled")
        serial_upload_button.config(state="disabled")
        progress_var.set(0)
        status_var.set("Starte...")
        threading.Thread(target=build_worker, args=(output_path,), daemon=True).start()

    def upload_worker(bundle_path, base_url):
        _debug(f"GUI upload_worker start: bundle_path={bundle_path} base_url={base_url}")
        def set_upload_progress(done, total):
            progress_var.set(done / total * 100 if total else 100)
            status_var.set(f"Lade Bundle hoch ({done}/{total})...")

        def report(done, total):
            root.after(0, set_upload_progress, done, total)

        try:
            finalize = upload_bundle_to_pico(bundle_path, base_url, progress_callback=report)

            def finish():
                progress_var.set(100)
                msg = finalize.get("message", "Upload abgeschlossen.")
                status_var.set(msg)
                upload_button.config(state="normal")
                lang_upload_button.config(state="normal")
                lang_serial_upload_button.config(state="normal")
                build_button.config(state="normal")
                license_build_button.config(state="normal")
                serial_upload_button.config(state="normal")
                messagebox.showinfo("OTA erfolgreich", msg)

            root.after(0, finish)
        except Exception as e:
            err_text = str(e)
            _debug(f"GUI upload_worker failed: {_shorten(err_text)}")

            def fail():
                status_var.set(f"Fehler beim OTA-Upload: {err_text}")
                upload_button.config(state="normal")
                lang_upload_button.config(state="normal")
                lang_serial_upload_button.config(state="normal")
                build_button.config(state="normal")
                license_build_button.config(state="normal")
                serial_upload_button.config(state="normal")
                messagebox.showerror("OTA-Fehler", err_text)

            root.after(0, fail)

    def start_upload():
        bundle_path = output_var.get().strip()
        _debug(f"GUI start_upload called: bundle_path={bundle_path}")
        if not bundle_path:
            messagebox.showerror("Fehler", "Bitte einen Bundle-Pfad angeben.")
            return
        if not os.path.isfile(bundle_path):
            messagebox.showerror("Fehler", f"Bundle nicht gefunden:\n{bundle_path}")
            return
        base_url = normalize_base_url(pico_url_var.get())
        upload_button.config(state="disabled")
        lang_upload_button.config(state="disabled")
        lang_serial_upload_button.config(state="disabled")
        build_button.config(state="disabled")
        license_build_button.config(state="disabled")
        serial_upload_button.config(state="disabled")
        progress_var.set(0)
        status_var.set(f"Starte OTA-Upload nach {base_url}...")
        threading.Thread(target=upload_worker, args=(bundle_path, base_url), daemon=True).start()

    def start_upload_language_pack():
        bundle_path = output_var.get().strip()
        if os.path.basename(bundle_path).lower() != LANGUAGE_BUNDLE_FILENAME:
            bundle_path = os.path.join(BUILD_DIR, LANGUAGE_BUNDLE_FILENAME)

        _debug(f"GUI start_upload_language_pack called: bundle_path={bundle_path}")
        if not os.path.isfile(bundle_path):
            messagebox.showerror(
                "Fehler",
                f"Sprachpaket nicht gefunden:\n{bundle_path}\n\n"
                "Erstelle zuerst ein Sprachpaket (Mode: Builde Sprachpaket).",
            )
            return

        base_url = normalize_base_url(pico_url_var.get())
        lang_upload_button.config(state="disabled")
        lang_serial_upload_button.config(state="disabled")
        upload_button.config(state="disabled")
        build_button.config(state="disabled")
        license_build_button.config(state="disabled")
        serial_upload_button.config(state="disabled")
        progress_var.set(0)
        status_var.set(f"Starte OTA-Upload von lang.pak nach {base_url}...")
        threading.Thread(target=upload_worker, args=(bundle_path, base_url), daemon=True).start()

    def serial_upload_worker(bundle_path):
        _debug(f"GUI serial_upload_worker start: bundle_path={bundle_path}")
        def set_serial_progress(done, total, message):
            progress_var.set(done / total * 100 if total else 100)
            status_var.set(message)

        def report(done, total, message):
            root.after(0, set_serial_progress, done, total, message)

        try:
            result = upload_bundle_via_serial(bundle_path, progress_callback=report)

            def finish():
                progress_var.set(100)
                msg = result.get("message", "Serieller Upload abgeschlossen.")
                status_var.set(msg)
                serial_upload_button.config(state="normal")
                upload_button.config(state="normal")
                lang_upload_button.config(state="normal")
                lang_serial_upload_button.config(state="normal")
                build_button.config(state="normal")
                license_build_button.config(state="normal")
                messagebox.showinfo("Serieller Upload erfolgreich", msg)

            root.after(0, finish)
        except Exception as e:
            err_text = str(e)
            _debug(f"GUI serial_upload_worker failed: {_shorten(err_text)}")

            def fail():
                status_var.set(f"Fehler beim seriellen Upload: {err_text}")
                serial_upload_button.config(state="normal")
                upload_button.config(state="normal")
                lang_upload_button.config(state="normal")
                lang_serial_upload_button.config(state="normal")
                build_button.config(state="normal")
                license_build_button.config(state="normal")
                messagebox.showerror("Serieller Upload-Fehler", err_text)

            root.after(0, fail)

    def start_serial_upload():
        bundle_path = output_var.get().strip()
        _debug(f"GUI start_serial_upload called: bundle_path={bundle_path}")
        if not bundle_path:
            messagebox.showerror("Fehler", "Bitte einen Bundle-Pfad angeben.")
            return
        if not os.path.isfile(bundle_path):
            messagebox.showerror("Fehler", f"Bundle nicht gefunden:\n{bundle_path}")
            return

        serial_upload_button.config(state="disabled")
        upload_button.config(state="disabled")
        lang_upload_button.config(state="disabled")
        lang_serial_upload_button.config(state="disabled")
        build_button.config(state="disabled")
        license_build_button.config(state="disabled")
        progress_var.set(0)
        status_var.set("Suche Pico ueber USB-Seriell...")
        threading.Thread(target=serial_upload_worker, args=(bundle_path,), daemon=True).start()

    def start_serial_upload_language_pack():
        bundle_path = output_var.get().strip()
        if os.path.basename(bundle_path).lower() != LANGUAGE_BUNDLE_FILENAME:
            bundle_path = os.path.join(BUILD_DIR, LANGUAGE_BUNDLE_FILENAME)

        _debug(f"GUI start_serial_upload_language_pack called: bundle_path={bundle_path}")
        if not os.path.isfile(bundle_path):
            messagebox.showerror(
                "Fehler",
                f"Sprachpaket nicht gefunden:\n{bundle_path}\n\n"
                "Erstelle zuerst ein Sprachpaket (Mode: Builde Sprachpaket).",
            )
            return

        lang_serial_upload_button.config(state="disabled")
        serial_upload_button.config(state="disabled")
        upload_button.config(state="disabled")
        lang_upload_button.config(state="disabled")
        build_button.config(state="disabled")
        license_build_button.config(state="disabled")
        progress_var.set(0)
        status_var.set("Suche Pico ueber USB-Seriell fuer lang.pak...")
        threading.Thread(target=serial_upload_worker, args=(bundle_path,), daemon=True).start()

    def license_build_worker(customer_id, regenerate_license):
        _debug(f"GUI license_build_worker start: customer_id={customer_id} regenerate={regenerate_license}")

        def report(done, total, message):
            def update():
                progress_var.set(done / total * 100 if total else 100)
                status_var.set(message)
            root.after(0, update)

        try:
            result = build_and_flash_with_license(
                customer_id, regenerate_license=regenerate_license, progress_callback=report,
            )

            def finish():
                progress_var.set(100)
                msg = f"Fertig ({result['port']}). Hardware-ID: {result['hardware_id']}."
                if result["license_issued"]:
                    msg += f" Neue Lizenz ausgestellt und archiviert: {result['license_record_path']}"
                if result["version_warning"]:
                    msg += f"\nWARNUNG: {result['version_warning']}"
                status_var.set(msg)
                build_button.config(state="normal")
                upload_button.config(state="normal")
                lang_upload_button.config(state="normal")
                lang_serial_upload_button.config(state="normal")
                serial_upload_button.config(state="normal")
                license_build_button.config(state="normal")
                messagebox.showinfo("Firmware + Lizenz installiert", msg)

            root.after(0, finish)
        except Exception as e:
            err_text = str(e)
            _debug(f"GUI license_build_worker failed: {_shorten(err_text)}")

            def fail():
                status_var.set(f"Fehler: {err_text}")
                build_button.config(state="normal")
                upload_button.config(state="normal")
                lang_upload_button.config(state="normal")
                lang_serial_upload_button.config(state="normal")
                serial_upload_button.config(state="normal")
                license_build_button.config(state="normal")
                messagebox.showerror("Fehler", err_text)

            root.after(0, fail)

    def start_license_build():
        if regenerate_license_var.get() and not keys_exist():
            messagebox.showerror("Fehler", "Kein Schluesselpaar vorhanden. Bitte zuerst 'Schluesselpaar erzeugen'.")
            return
        build_button.config(state="disabled")
        upload_button.config(state="disabled")
        lang_upload_button.config(state="disabled")
        lang_serial_upload_button.config(state="disabled")
        serial_upload_button.config(state="disabled")
        license_build_button.config(state="disabled")
        progress_var.set(0)
        status_var.set("Suche Pico ueber USB-Seriell...")
        threading.Thread(
            target=license_build_worker,
            args=(customer_id_var.get().strip(), regenerate_license_var.get()),
            daemon=True,
        ).start()

    build_button.config(command=start_build)
    upload_button.config(command=start_upload)
    lang_upload_button.config(command=start_upload_language_pack)
    lang_serial_upload_button.config(command=start_serial_upload_language_pack)
    serial_upload_button.config(command=start_serial_upload)
    license_build_button.config(command=start_license_build)

    root.mainloop()


def main():
    if len(sys.argv) == 1:
        try:
            launch_gui()
        except Exception as e:
            print(f"GUI konnte nicht gestartet werden ({e}), verwende Kommandozeilen-Modus.")
            run_cli(None)
        return

    parser = argparse.ArgumentParser(description="FPV Firmware Bundle Builder")
    parser.add_argument("output_path", nargs="?", default=os.path.join(BUILD_DIR, "firmware.nbo"))
    parser.add_argument("--mode", choices=["normal", "complete", "light", "recovery", "lang", "bootmain"], default="normal")
    parser.add_argument("--no-version-bump", action="store_true", help="Version nicht automatisch erhoehen")
    args = parser.parse_args()

    # Ein reiner Dateiname ohne Verzeichnisanteil (z.B. "firmware.nbo") landet
    # immer in BUILD_DIR statt im aktuellen Arbeitsverzeichnis (Projekt-Root) -
    # verhindert versehentlich im Root abgelegte .nbo-Dateien bei manuellen
    # CLI-Aufrufen wie "python build_firmware.py firmware.nbo".
    if os.path.dirname(args.output_path) == "":
        args.output_path = os.path.join(BUILD_DIR, args.output_path)

    include_boot_stack = False
    light_mode = False
    recovery_mode = False
    language_pack_mode = False
    boot_main_only_mode = False
    if args.mode == "complete":
        include_boot_stack = True
    elif args.mode == "light":
        light_mode = True
    elif args.mode == "recovery":
        recovery_mode = True
    elif args.mode == "lang":
        language_pack_mode = True
        if args.output_path == os.path.join(BUILD_DIR, "firmware.nbo"):
            args.output_path = os.path.join(BUILD_DIR, LANGUAGE_BUNDLE_FILENAME)
    elif args.mode == "bootmain":
        boot_main_only_mode = True

    run_cli(
        args.output_path,
        include_boot_stack=include_boot_stack,
        light_mode=light_mode,
        recovery_mode=recovery_mode,
        language_pack_mode=language_pack_mode,
        boot_main_only_mode=boot_main_only_mode,
        bump_version=(not args.no_version_bump),
    )


if __name__ == "__main__":
    main()
