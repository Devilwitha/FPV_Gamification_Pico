"""Musik-Bibliothek fuer den Blackbox Viewer (Reiter "Ton & Musik").

Musikdateien legt der Betreiber einfach in den Ordner MUSIC_DIR (Standard:
<Repo>/musik, Unterordner erlaubt - sie erscheinen als Gruppe). Der Viewer holt
die Liste ueber GET /api/musik und die Dateien ueber GET /api/musik/datei/<pfad>.
Neue oder geloeschte Dateien sind sofort sichtbar, ein Neustart ist nicht noetig.
Wie die Bibliothek: nur Musik ablegen, die man teilen darf - die Liste ist oeffentlich.
"""

import mimetypes
import os
from pathlib import Path

from flask import Blueprint, abort, jsonify, redirect, request, send_from_directory, session, url_for

import db

MUSIC_DIR = db.media_dir("MUSIC_DIR", "musik")
AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".weba": "audio/webm",
}
MAX_TRACKS = 500
MAX_FILE_BYTES = 200 * 1024 * 1024
README = """Musik fuer den Blackbox Viewer (Reiter "Ton & Musik")

Musikdateien einfach in diesen Ordner kopieren: MP3, M4A, AAC, OGG, OPUS, WAV oder FLAC.
Unterordner erscheinen im Viewer als Gruppe (z. B. "Chill/Song.mp3").
Der Dateiname wird zum Titel: "Mein_Song - Remix.mp3" -> "Mein Song - Remix".
Neue Dateien sind sofort in der Liste, ein Neustart ist nicht noetig.

Wichtig: Die Liste und die Dateien sind fuer alle Besucher abrufbar, und Videos mit
Musik landen im oeffentlichen Leaderboard. Nur Musik ablegen, die geteilt werden darf
(zum Beispiel lizenzfreie Musik).
"""

bp = Blueprint("musik", __name__)


def ensure_dir():
    """Ordner samt Anleitung anlegen (beim Start)."""
    try:
        MUSIC_DIR.mkdir(parents=True, exist_ok=True)
        readme = MUSIC_DIR / "LIESMICH.txt"
        if not readme.exists():
            readme.write_text(README, encoding="utf-8")
    except OSError:
        pass


def title_of(path):
    """Dateiname -> Titel: Endung weg, Unterstriche zu Leerzeichen."""
    return " ".join(Path(path).stem.replace("_", " ").split()) or Path(path).name


def tracks():
    """Alle Musikdateien, sortiert nach Gruppe und Titel."""
    if not MUSIC_DIR.is_dir():
        return []
    root = MUSIC_DIR.resolve()
    found = []
    for path in root.rglob("*"):
        if len(found) >= MAX_TRACKS:
            break
        rel = path.relative_to(root)
        if any(part.startswith(".") for part in rel.parts) or path.suffix.lower() not in AUDIO_TYPES:
            continue
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
        except OSError:
            continue
        if size <= 0 or size > MAX_FILE_BYTES:
            continue
        group = "/".join(rel.parts[:-1])
        found.append({
            "id": rel.as_posix(),
            "title": title_of(path.name),
            "group": group,
            "size": size,
            "type": AUDIO_TYPES[path.suffix.lower()],
        })
    found.sort(key=lambda t: (t["group"].lower(), t["title"].lower()))
    return found


def resolve(name):
    """Sicheren Pfad innerhalb von MUSIC_DIR liefern, sonst None."""
    if not name or "\\" in name or name.startswith("/") or "\x00" in name:
        return None
    parts = name.split("/")
    if any(p in ("", ".", "..") or p.startswith(".") for p in parts):
        return None
    root = MUSIC_DIR.resolve()
    path = root.joinpath(*parts).resolve()
    if root not in path.parents or path.suffix.lower() not in AUDIO_TYPES or not path.is_file():
        return None
    return path


@bp.get("/api/musik")
def api_list():
    response = jsonify({"tracks": tracks()})
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/api/musik/datei/<path:name>")
def api_file(name):
    path = resolve(name)
    if path is None:
        abort(404)
    root = MUSIC_DIR.resolve()
    mime = AUDIO_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return send_from_directory(root, path.relative_to(root).as_posix(), mimetype=mime, conditional=True, max_age=86400)


# --- Verwaltung im Admin-Dashboard (Reiter "Musik") ----------------------

MAX_UPLOAD_BYTES = 900 * 1024 * 1024   # je Upload (mehrere Dateien), IIS: location admin/musik
MAX_FOLDER_DEPTH = 2
NAME_MAX = 120
# Erkennung am Dateianfang - die Endung allein reicht nicht.
_ID3, _OGG, _FLAC, _RIFF, _EBML = b"ID3", b"OggS", b"fLaC", b"RIFF", b"\x1a\x45\xdf\xa3"


class MusicError(ValueError):
    pass


def looks_like_audio(ext, head):
    """Passt der Dateianfang zur Endung?"""
    mpeg_frame = len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0
    if ext == ".mp3":
        return head.startswith(_ID3) or mpeg_frame
    if ext == ".aac":
        return head.startswith(_ID3) or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xF6) == 0xF0)
    if ext in (".m4a",):
        return head[4:8] == b"ftyp"
    if ext in (".ogg", ".oga", ".opus"):
        return head.startswith(_OGG)
    if ext == ".wav":
        return head.startswith(_RIFF) and head[8:12] == b"WAVE"
    if ext == ".flac":
        return head.startswith(_FLAC) or (head.startswith(_ID3) and b"fLaC" in head)
    if ext == ".weba":
        return head.startswith(_EBML)
    return False


def clean_part(text):
    """Datei- oder Ordnername: Buchstaben (auch Umlaute), Ziffern, Leerzeichen und - _ . ( ) ' & !"""
    out = "".join(ch for ch in (text or "") if ch.isalnum() or ch in " -_.()'&!")
    out = " ".join(out.split()).strip(" .")
    return out[:NAME_MAX]


def clean_folder(text):
    """Unterordner aus der Eingabe (z. B. "Chill" oder "Chill/Sommer"); "" = Hauptordner."""
    parts = [clean_part(p) for p in (text or "").replace("\\", "/").split("/")]
    parts = [p for p in parts if p and not p.startswith(".")]
    if len(parts) > MAX_FOLDER_DEPTH:
        raise MusicError(f"Höchstens {MAX_FOLDER_DEPTH} Ordner-Ebenen.")
    return "/".join(parts)


def _target(folder, stem, ext, overwrite):
    root = MUSIC_DIR.resolve()
    directory = root.joinpath(*folder.split("/")) if folder else root
    path = directory / f"{stem}{ext}"
    n = 2
    while path.exists() and not overwrite:
        path = directory / f"{stem} ({n}){ext}"
        n += 1
    return directory, path


def save_upload(storage, folder="", overwrite=False):
    """Hochgeladene Datei pruefen und ablegen; liefert die id (relativer Pfad)."""
    original = Path(storage.filename or "").name
    ext = Path(original).suffix.lower()
    if ext not in AUDIO_TYPES:
        raise MusicError(f"„{original}“: nur MP3, M4A, AAC, OGG, OPUS, WAV oder FLAC.")
    stem = clean_part(Path(original).stem)
    if not stem or stem.startswith("."):
        raise MusicError(f"„{original}“: ungültiger Dateiname.")
    head = storage.stream.read(64)
    if not looks_like_audio(ext, head):
        raise MusicError(f"„{original}“ ist keine gültige {ext[1:].upper()}-Datei.")
    directory, path = _target(folder, stem, ext, overwrite)
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".upload-{os.getpid()}-{path.name}"
    size = len(head)
    try:
        with open(tmp, "wb") as out:
            out.write(head)
            while True:
                chunk = storage.stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise MusicError(f"„{original}“ ist grösser als {MAX_FILE_BYTES // (1024 * 1024)} MB.")
                out.write(chunk)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path.relative_to(MUSIC_DIR.resolve()).as_posix()


def _prune(directory):
    """Leer gewordene Unterordner entfernen (nie den Hauptordner)."""
    root = MUSIC_DIR.resolve()
    while directory != root and root in directory.parents:
        try:
            directory.rmdir()
        except OSError:
            return
        directory = directory.parent


def delete_track(track_id):
    path = resolve(track_id)
    if path is None:
        raise MusicError("Song nicht gefunden.")
    path.unlink()
    _prune(path.parent)
    return title_of(path.name)


def move_track(track_id, new_name, folder):
    """Umbenennen und/oder in einen anderen Ordner verschieben; liefert die neue id."""
    path = resolve(track_id)
    if path is None:
        raise MusicError("Song nicht gefunden.")
    stem = clean_part(new_name)
    if not stem or stem.startswith("."):
        raise MusicError("Bitte einen gültigen Namen angeben.")
    folder = clean_folder(folder)
    root = MUSIC_DIR.resolve()
    ext = path.suffix.lower()
    directory = root.joinpath(*folder.split("/")) if folder else root
    target = directory / f"{stem}{ext}"
    # Dieselbe Datei (auch nur Gross-/Kleinschreibung geaendert, Windows) ist kein Namenskonflikt
    if target.exists() and not os.path.samefile(target, path):
        directory, target = _target(folder, stem, ext, overwrite=False)
    if str(target) == str(path):
        return track_id
    directory.mkdir(parents=True, exist_ok=True)
    os.replace(path, target)
    _prune(path.parent)
    return target.relative_to(MUSIC_DIR.resolve()).as_posix()


def admin_overview():
    items = tracks()
    folders = sorted({t["group"] for t in items if t["group"]}, key=str.lower)
    return {
        "tracks": items,
        "folders": folders,
        "total_bytes": sum(t["size"] for t in items),
        "dir": str(MUSIC_DIR),
        "max_file_mb": MAX_FILE_BYTES // (1024 * 1024),
        "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024),
    }


def _admin_back(message):
    return redirect(url_for("admin_dashboard", ok=message) + "#musik")


@bp.post("/admin/musik/hochladen")
def admin_upload():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    if (request.content_length or 0) > MAX_UPLOAD_BYTES:
        return _admin_back(f"Zu gross: höchstens {MAX_UPLOAD_BYTES // (1024 * 1024)} MB pro Upload.")
    files = [f for f in request.files.getlist("musik") if f and f.filename]
    if not files:
        return _admin_back("Bitte mindestens eine Musikdatei wählen.")
    try:
        folder = clean_folder(request.form.get("ordner", ""))
    except MusicError as exc:
        return _admin_back(str(exc))
    overwrite = request.form.get("ueberschreiben") == "1"
    saved, errors = [], []
    for storage in files:
        try:
            saved.append(save_upload(storage, folder, overwrite))
        except MusicError as exc:
            errors.append(str(exc))
        except OSError as exc:
            errors.append(f"„{storage.filename}“ konnte nicht gespeichert werden ({exc.strerror or exc}).")
    parts = []
    if saved:
        parts.append(f"{len(saved)} Song{'s' if len(saved) != 1 else ''} hochgeladen" + (f" nach „{folder}“" if folder else "") + ".")
    parts.extend(errors)
    return _admin_back(" ".join(parts))


@bp.post("/admin/musik/aendern")
def admin_change():
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    track_id = request.form.get("id", "")
    try:
        if request.form.get("aktion") == "loeschen":
            title = delete_track(track_id)
            return _admin_back(f"„{title}“ gelöscht.")
        new_id = move_track(track_id, request.form.get("name", ""), request.form.get("ordner", ""))
        return _admin_back(f"Gespeichert als „{new_id}“.")
    except MusicError as exc:
        return _admin_back(str(exc))
    except OSError as exc:
        return _admin_back(f"Ändern fehlgeschlagen ({exc.strerror or exc}).")


def register(app):
    app.register_blueprint(bp)
    ensure_dir()
