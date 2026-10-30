"""Propwash FPV-Simulator: Download der Offline-Version als ZIP (/fpv-simulator.zip).

Der Simulator selbst ist eine statische Seite der Drohnen Tools:
/tx15-werkstatt/fpv-simulator.html, Quellen in drohnen-tools/fpv-simulator/
(build.py baut daraus die Webseite und offline/propwash-fpv-simulator.html).
Das ZIP enthaelt die Offline-Datei, die Anleitung, die three.js-Lizenz und die
Quellen - so wie es in der LIESMICH.md beschrieben ist. Es wird bei Bedarf aus
den Dateien im Repo gepackt und im Speicher gehalten, bis sich eine Datei aendert.
"""

import io
import json
import threading
import time
import zipfile
from pathlib import Path

from flask import Blueprint, Response, abort, jsonify, request, session

import db
import roles
from tx15_backups import account_email

SIM_DIR = Path(__file__).resolve().parent.parent / "drohnen-tools" / "fpv-simulator"
ROOT = "propwash-fpv-simulator"
# Pfad im ZIP -> Datei im Repo
FILES = (
    (f"{ROOT}/propwash-fpv-simulator.html", "offline/propwash-fpv-simulator.html"),
    (f"{ROOT}/LIESMICH.md", "LIESMICH.md"),
    (f"{ROOT}/LICENSE-three.js.txt", "vendor/LICENSE-three.js.txt"),
    (f"{ROOT}/quellcode/sim-core.js", "sim-core.js"),
    (f"{ROOT}/quellcode/simulator.src.html", "simulator.src.html"),
    (f"{ROOT}/quellcode/build.py", "build.py"),
    (f"{ROOT}/quellcode/vendor/three.module.min.js", "vendor/three.module.min.js"),
    (f"{ROOT}/quellcode/vendor/LICENSE-three.js.txt", "vendor/LICENSE-three.js.txt"),
)

bp = Blueprint("fpv_simulator", __name__)
_cache = {"key": None, "data": None}
_lock = threading.Lock()


def _signature():
    try:
        return tuple((name, (SIM_DIR / src).stat().st_mtime_ns, (SIM_DIR / src).stat().st_size) for name, src in FILES)
    except OSError:
        return None


def build_zip():
    """ZIP-Bytes oder None, wenn eine Datei fehlt (z. B. build.py nicht gelaufen)."""
    key = _signature()
    if key is None:
        return None
    with _lock:
        if _cache["key"] != key:
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
                for name, src in FILES:
                    zf.write(SIM_DIR / src, name)
            _cache["key"], _cache["data"] = key, buf.getvalue()
        return _cache["data"]


# --- Einstellungen im Konto ------------------------------------------------
# Der Simulator laeuft im Browser jedes Besuchers - jeder fliegt seine eigene Instanz.
# Eingeloggt speichert er Einstellungen, Sender-Zuordnung, Kalibrierung und Bestzeiten
# hier im Konto (eine Zeile je Konto), sonst nur im Browser.

SETTINGS_URL = "/api/fpv-simulator/einstellungen"
SIM_PAGE = "/tx15-werkstatt/fpv-simulator.html"
AJAX_HEADER_VALUE = "fpv-simulator"
MAX_SETTINGS_BYTES = 256 * 1024


def _no_store(response):
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get(SETTINGS_URL)
def settings_get():
    email = account_email()
    payload = {"loggedIn": False, "loginUrl": f"/login?next={SIM_PAGE}", "settings": None, "updatedAt": None}
    if email:
        account = db.get_account_by_email(email)
        payload.update(loggedIn=True, username=(account["username"] if account else None) or "")
        row = db.sim_settings_get(email)
        if row:
            try:
                payload["settings"] = json.loads(row["data"])
            except ValueError:
                payload["settings"] = None
            payload["updatedAt"] = row["updated_at"]
    return _no_store(jsonify(payload))


@bp.put(SETTINGS_URL)
def settings_put():
    if request.headers.get("X-Requested-With") != AJAX_HEADER_VALUE:
        return jsonify({"error": "Ungültige Anfrage."}), 403
    email = account_email()
    if not email:
        return jsonify({"error": "Zum Speichern im Konto bitte einloggen.", "loginUrl": f"/login?next={SIM_PAGE}"}), 401
    if (request.content_length or 0) > MAX_SETTINGS_BYTES:
        return jsonify({"error": "Einstellungen zu gross."}), 413
    body = request.get_json(silent=True)
    settings = body.get("settings") if isinstance(body, dict) else None
    if not isinstance(settings, dict):
        return jsonify({"error": "Keine Einstellungen erhalten."}), 400
    data = json.dumps(settings, ensure_ascii=False, separators=(",", ":"))
    if len(data.encode("utf-8")) > MAX_SETTINGS_BYTES:
        return jsonify({"error": "Einstellungen zu gross."}), 413
    updated = db.sim_settings_save(email, data)
    return _no_store(jsonify({"ok": True, "updatedAt": updated}))


# --- Maps aus dem Map-Editor -----------------------------------------------
# Format (Version 1): {"v": 1, "start": {"x", "z", "r"}, "objects": [{"t", "x", "z", "r", "s", "h"}]}
# x/z in Metern (Kartenmitte 0/0, -z = Norden), r = Drehung in Grad, s = Groesse/Variante, h = Hoehe (Gates).
# Der Server speichert nur, was er selbst nachgebaut hat - nie die Rohdaten des Browsers.

MAPS_URL = "/api/fpv-simulator/maps"
# Teile und ihre Felder: Feld -> (min, max, Standard, "i" ganzzahlig | "f" Meter mit 1 Nachkommastelle)
# s = Groesse/Farbvariante, h = Hoehenstufe (Gates); Formen: w/d = Breite/Tiefe, hh = Hoehe, y = ueber Boden, m = Material
SHAPE_FIELDS = {
    "w": (0.2, 40, 4, "f"), "d": (0.2, 40, 4, "f"), "hh": (0.1, 20, 3, "f"), "y": (0, 30, 0, "f"), "m": (0, 10, 0, "i"),
}
MAP_TYPES = {
    "gate": {"s": (1, 3, 1, "i"), "h": (0, 3, 0, "i")},
    "flag": {},
    "tree": {"s": (1, 3, 2, "i")},
    "pine": {"s": (1, 3, 2, "i")},
    "wall": {},
    "block": {},
    "container": {"s": (1, 3, 1, "i")},
    "ruin": {},
    "box": SHAPE_FIELDS,   # Quader / Platte
    "cyl": SHAPE_FIELDS,   # Saeule (w = Durchmesser)
    "ramp": SHAPE_FIELDS,  # schraege Flaeche: steigt ueber die Tiefe d um hh an
}
MAP_EXTENT = 140.0
MAX_OBJECTS, MAX_GATES, MAX_RUINS = 300, 40, 3
MAX_MAPS_PER_USER = 50
MAP_NAME_MAX = 40
MAX_MAP_BYTES = 64 * 1024
SAVE_INTERVAL = 3          # Sekunden zwischen zwei neuen Maps eines Kontos


class MapError(ValueError):
    pass


def _num(value, lo, hi, digits=1):
    """Koordinate auf den erlaubten Bereich klemmen; Unsinn (Text, NaN) wird 0 wie im Browser."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = 0.0
    if v != v:  # NaN
        v = 0.0
    return round(min(hi, max(lo, v)), digits)


def _int(value, lo, hi, default):
    try:
        return min(hi, max(lo, int(value)))
    except (TypeError, ValueError):
        return default


def _angle(value):
    """Drehung in ganzen Grad 0..359; Unsinn (Text, NaN, unendlich) wird 0."""
    try:
        return int(round(float(value))) % 360
    except (TypeError, ValueError, OverflowError):
        return 0


def clean_name(name):
    text = " ".join(str(name or "").split())
    text = "".join(ch for ch in text if ch.isprintable())[:MAP_NAME_MAX].strip()
    if len(text) < 2:
        raise MapError("Bitte gib der Map einen Namen (mindestens 2 Zeichen).")
    return text


def sanitize_map(data):
    """Map pruefen und neu aufbauen; liefert (map, gates, objects) oder wirft MapError."""
    if not isinstance(data, dict):
        raise MapError("Keine Map erhalten.")
    start = data.get("start") if isinstance(data.get("start"), dict) else {}
    out = {
        "v": 1,
        "start": {
            "x": _num(start.get("x", 0), -MAP_EXTENT, MAP_EXTENT),
            "z": _num(start.get("z", 0), -MAP_EXTENT, MAP_EXTENT),
            "r": _angle(start.get("r", 0)),
        },
        "objects": [],
    }
    objects = data.get("objects")
    if not isinstance(objects, list):
        raise MapError("Keine Map erhalten.")
    if len(objects) > MAX_OBJECTS:
        raise MapError(f"Höchstens {MAX_OBJECTS} Teile pro Map.")
    gates = ruins = 0
    for obj in objects:
        if not isinstance(obj, dict) or obj.get("t") not in MAP_TYPES:
            raise MapError("Unbekanntes Teil in der Map.")
        kind = obj["t"]
        item = {
            "t": kind,
            "x": _num(obj.get("x"), -MAP_EXTENT, MAP_EXTENT),
            "z": _num(obj.get("z"), -MAP_EXTENT, MAP_EXTENT),
            "r": _angle(obj.get("r", 0)),
        }
        for field, (lo, hi, default, typ) in MAP_TYPES[kind].items():
            value = obj.get(field, default)
            item[field] = _int(value, lo, hi, default) if typ == "i" else _num(value, lo, hi)
        gates += kind == "gate"
        ruins += kind == "ruin"
        out["objects"].append(item)
    if gates > MAX_GATES:
        raise MapError(f"Höchstens {MAX_GATES} Gates pro Map.")
    if ruins > MAX_RUINS:
        raise MapError(f"Höchstens {MAX_RUINS} Ruinen pro Map.")
    return out, gates, len(out["objects"])


def _can_moderate():
    return roles.is_moderator()


def _summary(row, viewer):
    return {
        "id": row["id"],
        "name": row["name"],
        "author": row["author"] or "",
        "gates": row["gates"],
        "objects": row["objects"],
        "shared": bool(row["shared"]),
        "plays": row["plays"],
        "updatedAt": row["updated_at"],
        "mine": bool(viewer) and (row["owner_email"] or "").lower() == viewer,
        "link": f"{SIM_PAGE}?map={row['id']}",
    }


def _require_write():
    if request.headers.get("X-Requested-With") != AJAX_HEADER_VALUE:
        return None, (jsonify({"error": "Ungültige Anfrage."}), 403)
    email = account_email()
    if not email:
        return None, (jsonify({"error": "Zum Speichern und Teilen bitte einloggen.", "loginUrl": f"/login?next={SIM_PAGE}"}), 401)
    if (request.content_length or 0) > MAX_MAP_BYTES * 2:
        return None, (jsonify({"error": "Map zu gross."}), 413)
    return email, None


def _parse_body():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise MapError("Keine Map erhalten.")
    name = clean_name(body.get("name"))
    data, gates, objects = sanitize_map(body.get("data"))
    raw = json.dumps(data, separators=(",", ":"))
    if len(raw) > MAX_MAP_BYTES:
        raise MapError("Map zu gross.")
    return name, raw, gates, objects, bool(body.get("shared"))


@bp.get(MAPS_URL)
def maps_list():
    viewer = account_email()
    which = request.args.get("liste", "community")
    if which == "meine":
        rows = db.sim_map_list_mine(viewer) if viewer else []
    else:
        rows = db.sim_map_list_shared("beliebt" if request.args.get("sort") == "beliebt" else "neu")
    return _no_store(jsonify({
        "maps": [_summary(r, viewer) for r in rows],
        "loggedIn": bool(viewer),
        "canModerate": _can_moderate(),
        "limits": {"objects": MAX_OBJECTS, "gates": MAX_GATES, "maps": MAX_MAPS_PER_USER},
    }))


@bp.get(MAPS_URL + "/<int:map_id>")
def maps_get(map_id):
    viewer = account_email()
    row = db.sim_map_get(map_id)
    if row is None:
        abort(404)
    mine = bool(viewer) and (row["owner_email"] or "").lower() == viewer
    if not row["shared"] and not mine and not _can_moderate():
        abort(404)
    data = _summary(row, viewer)
    data["data"] = json.loads(row["data"])
    return _no_store(jsonify({"map": data}))


@bp.post(MAPS_URL)
def maps_create():
    email, error = _require_write()
    if error:
        return error
    last = session.get("sim_map_saved_at", 0)
    if time.time() - last < SAVE_INTERVAL:
        return jsonify({"error": "Bitte kurz warten und dann nochmals speichern."}), 429
    if db.sim_map_count(email) >= MAX_MAPS_PER_USER:
        return jsonify({"error": f"Höchstens {MAX_MAPS_PER_USER} Maps pro Konto. Lösche zuerst eine alte Map."}), 400
    try:
        name, raw, gates, objects, shared = _parse_body()
    except MapError as exc:
        return jsonify({"error": str(exc)}), 400
    session["sim_map_saved_at"] = time.time()
    map_id = db.sim_map_create(email, name, raw, gates, objects, shared)
    return _no_store(jsonify({"map": _summary(db.sim_map_get(map_id, with_data=False), email)})), 201


@bp.put(MAPS_URL + "/<int:map_id>")
def maps_update(map_id):
    email, error = _require_write()
    if error:
        return error
    row = db.sim_map_get(map_id, with_data=False)
    if row is None or (row["owner_email"] or "").lower() != email:
        return jsonify({"error": "Diese Map gehört dir nicht – speichere sie als Kopie."}), 403
    try:
        name, raw, gates, objects, shared = _parse_body()
    except MapError as exc:
        return jsonify({"error": str(exc)}), 400
    db.sim_map_update(map_id, name, raw, gates, objects, shared)
    return _no_store(jsonify({"map": _summary(db.sim_map_get(map_id, with_data=False), email)}))


@bp.delete(MAPS_URL + "/<int:map_id>")
def maps_delete(map_id):
    if request.headers.get("X-Requested-With") != AJAX_HEADER_VALUE:
        return jsonify({"error": "Ungültige Anfrage."}), 403
    email = account_email()
    row = db.sim_map_get(map_id, with_data=False)
    if row is None:
        abort(404)
    mine = bool(email) and (row["owner_email"] or "").lower() == email
    if not mine and not _can_moderate():
        return jsonify({"error": "Nur der Ersteller oder die Moderation kann diese Map löschen."}), 403
    db.sim_map_delete(map_id)
    return _no_store(jsonify({"ok": True}))


@bp.post(MAPS_URL + "/<int:map_id>/gespielt")
def maps_played(map_id):
    """Zaehlt, wie oft eine geteilte Map geflogen wurde - je Sitzung einmal pro Map."""
    if request.headers.get("X-Requested-With") != AJAX_HEADER_VALUE:
        return jsonify({"error": "Ungültige Anfrage."}), 403
    row = db.sim_map_get(map_id, with_data=False)
    if row is None or not row["shared"]:
        abort(404)
    played = session.get("sim_maps_played", [])
    if map_id not in played:
        db.sim_map_played(map_id)
        session["sim_maps_played"] = (played + [map_id])[-200:]
    return _no_store(jsonify({"ok": True}))


@bp.get("/fpv-simulator.zip")
def download():
    data = build_zip()
    if data is None:
        abort(404)
    response = Response(data, mimetype="application/zip")
    response.headers["Content-Disposition"] = 'attachment; filename="propwash-fpv-simulator.zip"'
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response
