"""TX15-Firmware fuer das Backup Studio (Drohnen Tools).

Die RadioMaster TX15 laeuft mit EdgeTX. Die offizielle Firmware kommt aus den
Releases von github.com/EdgeTX/edgetx: Jedes Release enthaelt ein ZIP
(edgetx-firmware-<version>.zip) mit allen Zielen, fuer die TX15 die Datei
tx15-<commit>.uf2. Der Browser kann dieses ZIP wegen fehlender CORS-Header
nicht selbst laden, deshalb holt der Server es, entnimmt die TX15-Datei und
liefert nur diese aus (zwischengespeichert unter FIRMWARE_CACHE_DIR).

Installiert wird im Browser (backup-studio.html): Sender im Bootloader,
Laufwerk EDGETX_UF2 waehlen, .uf2 darauf schreiben - siehe
https://manual.edgetx.org/installing-and-updating-edgetx/updating-your-stm32h7-radio

Oeffentlich (ohne Login), da die Firmware selbst oeffentlich ist.
"""

import json
import os
import re
import struct
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import requests
from flask import Blueprint, jsonify, send_file

from tx15_backups import BACKUP_DIR, error_response
from i18n import gettext as _

RELEASES_URL = "https://api.github.com/repos/EdgeTX/edgetx/releases?per_page=30"
RELEASE_CACHE_SECONDS = 3600
MAX_RELEASES = 8
# Die offiziellen EdgeTX-Releases enthalten die TX15 erst ab 2.12 (2.11.x ohne tx15-*.uf2).
MIN_VERSION = (2, 12, 0)
MAX_ZIP_BYTES = 300 * 1024 * 1024
TARGET_PREFIX = "tx15-"
TARGET_NAME = "RadioMaster TX15"
FIRMWARE_CACHE_DIR = Path(os.environ.get("TX15_FIRMWARE_CACHE_DIR", "").strip() or BACKUP_DIR / "firmware-cache")
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
UF2_MAGIC_START0 = 0x0A324655
UF2_MAGIC_START1 = 0x9E5D5157
UF2_MAGIC_END = 0x0AB16F30
HTTP_HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "bollisoft-drohnen-tools"}

bp = Blueprint("tx15_firmware", __name__, url_prefix="/api/tx15-firmware")

_release_cache = {"time": 0.0, "releases": []}
_release_lock = threading.Lock()
_download_locks = {}
_download_locks_guard = threading.Lock()


def _version(tag):
    match = TAG_RE.match(tag or "")
    return tuple(int(part) for part in match.groups()) if match else None


def _firmware_asset(release):
    for asset in release.get("assets") or []:
        name = asset.get("name") or ""
        if name.startswith("edgetx-firmware-") and name.endswith(".zip"):
            return asset
    return None


def _releases():
    """Stabile EdgeTX-Releases mit Firmware-ZIP, neueste zuerst (1 h gepuffert)."""
    with _release_lock:
        if time.time() - _release_cache["time"] < RELEASE_CACHE_SECONDS and _release_cache["releases"]:
            return _release_cache["releases"]
        try:
            response = requests.get(RELEASES_URL, headers=HTTP_HEADERS, timeout=20)
            response.raise_for_status()
            releases = []
            for release in response.json():
                version = _version(release.get("tag_name"))
                asset = _firmware_asset(release)
                if release.get("draft") or release.get("prerelease") or version is None or asset is None:
                    continue
                if version < MIN_VERSION:
                    continue
                releases.append({
                    "tag": release["tag_name"],
                    "name": release.get("name") or release["tag_name"],
                    "published": (release.get("published_at") or "")[:10],
                    "url": release.get("html_url") or "",
                    "zip_url": asset["browser_download_url"],
                    "zip_size": asset.get("size") or 0,
                    "version": version,
                })
            releases.sort(key=lambda item: item["version"], reverse=True)
            _release_cache["releases"] = releases[:MAX_RELEASES]
            _release_cache["time"] = time.time()
        except (requests.RequestException, ValueError, KeyError):
            # Bei Fehlern den alten Stand weiterverwenden (falls vorhanden).
            if not _release_cache["releases"]:
                raise
        return _release_cache["releases"]


def _is_uf2(path):
    size = path.stat().st_size
    if size == 0 or size % 512:
        return False
    with path.open("rb") as handle:
        block = handle.read(512)
    start0, start1 = struct.unpack_from("<II", block, 0)
    (end,) = struct.unpack_from("<I", block, 508)
    return start0 == UF2_MAGIC_START0 and start1 == UF2_MAGIC_START1 and end == UF2_MAGIC_END


def _extract_tx15(zip_path, target):
    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        if "fw.json" in names:
            targets = json.loads(archive.read("fw.json")).get("targets") or []
            if not any(isinstance(t, list) and len(t) >= 2 and t[1] == TARGET_PREFIX for t in targets):
                raise LookupError(_("Dieses Release enthält keine Firmware für die TX15."))
        candidates = [n for n in names if n.startswith(TARGET_PREFIX) and n.endswith(".uf2") and "/" not in n]
        if len(candidates) != 1:
            raise LookupError(_("Dieses Release enthält keine Firmware für die TX15."))
        temp = target.with_suffix(".part")
        with archive.open(candidates[0]) as source, temp.open("wb") as handle:
            while chunk := source.read(1024 * 1024):
                handle.write(chunk)
    if not _is_uf2(temp):
        temp.unlink(missing_ok=True)
        raise LookupError(_("Die TX15-Datei im Release ist keine gültige UF2-Firmware."))
    os.replace(temp, target)


def _firmware_file(release):
    """Pfad der TX15-UF2 eines Releases, bei Bedarf erst herunterladen."""
    target = FIRMWARE_CACHE_DIR / f"edgetx-tx15-{release['tag']}.uf2"
    with _download_locks_guard:
        lock = _download_locks.setdefault(release["tag"], threading.Lock())
    with lock:
        if target.is_file():
            return target
        if release["zip_size"] > MAX_ZIP_BYTES:
            raise LookupError(_("Das Release-Archiv ist ungewöhnlich groß und wird nicht geladen."))
        FIRMWARE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(suffix=".zip", dir=FIRMWARE_CACHE_DIR)
        os.close(fd)
        zip_path = Path(temp_name)
        try:
            with requests.get(release["zip_url"], headers={"User-Agent": HTTP_HEADERS["User-Agent"]},
                              stream=True, timeout=60) as response:
                response.raise_for_status()
                written = 0
                with zip_path.open("wb") as handle:
                    for chunk in response.iter_content(1024 * 1024):
                        written += len(chunk)
                        if written > MAX_ZIP_BYTES:
                            raise LookupError(_("Das Release-Archiv ist ungewöhnlich groß und wird nicht geladen."))
                        handle.write(chunk)
            _extract_tx15(zip_path, target)
        finally:
            zip_path.unlink(missing_ok=True)
        return target


@bp.get("")
def list_firmware():
    try:
        releases = _releases()
    except (requests.RequestException, ValueError, KeyError):
        return error_response(_("Die Firmware-Liste von EdgeTX ist gerade nicht erreichbar."), 502)
    response = jsonify({
        "target": TARGET_NAME,
        "releases": [
            {key: release[key] for key in ("tag", "name", "published", "url")} for release in releases
        ],
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/<tag>/tx15.uf2")
def download_firmware(tag):
    try:
        release = next((r for r in _releases() if r["tag"] == tag), None)
    except (requests.RequestException, ValueError, KeyError):
        return error_response(_("Die Firmware-Liste von EdgeTX ist gerade nicht erreichbar."), 502)
    if release is None:
        return error_response(_("Diese Version gibt es nicht (mehr) zur Auswahl."), 404)
    try:
        path = _firmware_file(release)
    except LookupError as exc:
        return error_response(str(exc), 404)
    except (requests.RequestException, zipfile.BadZipFile, OSError, ValueError):
        return error_response(_("Die Firmware konnte nicht von EdgeTX geladen werden."), 502)
    response = send_file(
        path,
        mimetype="application/octet-stream",
        as_attachment=True,
        download_name=f"edgetx-tx15-{tag}.uf2",
        max_age=86400,
    )
    return response
