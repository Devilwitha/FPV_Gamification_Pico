"""Drohnen-Ausweis im Konto (/account/ausweis).

Ein Konto kann einen Nachweis hochladen (EU-Kompetenznachweis A1/A3,
Fernpiloten-Zeugnis A2, Betreiber-Registrierung, ...) als PDF oder Bild. Die
Datei ist privat: nur der Besitzer und der Admin koennen sie oeffnen, sie
liegt ausserhalb aller oeffentlich ausgelieferten Ordner (LICENSE_DIR,
Default <Repo>/private_media/licenses, siehe .gitignore).

Der Admin prueft den Ausweis im Admin-Dashboard; geprueft erscheint im
oeffentlichen Profil und neben Forenbeitraegen nur der Hinweis
"Drohnen-Ausweis geprueft" mit der Art - nie die Datei oder die Nummer.
"""

import io
import os
import uuid

from flask import Blueprint, abort, redirect, render_template, request, send_file, session, url_for
from PIL import Image, UnidentifiedImageError

import db
from community import LICENSE_KINDS
from konto import account_required
from i18n import gettext as _

LICENSE_DIR = db.media_dir("PILOT_LICENSE_DIR", "private_media", "licenses")
MAX_BYTES = 10 * 1024 * 1024
STATUS_LABELS = {"pending": "wird geprüft", "verified": "geprüft", "rejected": "abgelehnt"}

bp = Blueprint("ausweis", __name__)


def _detect(data):
    """(mime, ext) fuer PDF/JPG/PNG/WebP oder None."""
    if data[:5] == b"%PDF-":
        return "application/pdf", ".pdf"
    try:
        image = Image.open(io.BytesIO(data))
        image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return None
    return {"JPEG": ("image/jpeg", ".jpg"), "PNG": ("image/png", ".png"), "WEBP": ("image/webp", ".webp")}.get(image.format)


@bp.route("/account/ausweis", methods=["GET", "POST"])
@account_required
def page(account):
    email = account["email"]
    error = None
    if request.method == "POST" and request.form.get("action") == "delete":
        old = db.delete_pilot_license(email)
        if old:
            (LICENSE_DIR / old).unlink(missing_ok=True)
        return redirect(url_for("ausweis.page", ok="geloescht"))
    if request.method == "POST":
        kind = request.form.get("kind")
        number = (request.form.get("number") or "").strip()[:60]
        valid_until = (request.form.get("valid_until") or "").strip()[:10]
        upload = request.files.get("file")
        if kind not in LICENSE_KINDS:
            error = _("Bitte die Art des Nachweises wählen.")
        elif upload is None or not upload.filename:
            error = _("Bitte eine Datei (PDF oder Foto) auswählen.")
        else:
            data = upload.read(MAX_BYTES + 1)
            detected = _detect(data)
            if len(data) > MAX_BYTES:
                error = _("Die Datei ist zu groß (höchstens {v1} MB).", v1=MAX_BYTES // (1024 * 1024))
            elif detected is None:
                error = _("Bitte eine PDF-Datei oder ein Foto (JPG, PNG, WebP) hochladen.")
            else:
                LICENSE_DIR.mkdir(parents=True, exist_ok=True)
                filename = uuid.uuid4().hex + detected[1]
                (LICENSE_DIR / filename).write_bytes(data)
                old = db.save_pilot_license(email, kind, number, valid_until, filename,
                                            os.path.basename(upload.filename)[:120], detected[0], len(data))
                if old:
                    (LICENSE_DIR / old).unlink(missing_ok=True)
                return redirect(url_for("ausweis.page", ok="hochgeladen"))
    return render_template(
        "account_license.html",
        account=account,
        licence=db.get_pilot_license(email),
        kinds=LICENSE_KINDS,
        status_labels=STATUS_LABELS,
        error=error,
        message=request.args.get("ok"),
        max_mb=MAX_BYTES // (1024 * 1024),
    )


def _send(row):
    path = LICENSE_DIR / row["filename"]
    if not path.is_file():
        abort(404)
    response = send_file(path, mimetype=row["mime"], as_attachment=False,
                         download_name=row["original_name"] or path.name, max_age=0)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@bp.get("/account/ausweis/datei")
@account_required
def own_file(account):
    row = db.get_pilot_license(account["email"])
    if row is None:
        abort(404)
    return _send(row)


@bp.get("/admin/ausweise/<int:license_id>/datei")
def admin_file(license_id):
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    row = db.get_pilot_license_by_id(license_id)
    if row is None:
        abort(404)
    return _send(row)


@bp.post("/admin/ausweise/<int:license_id>")
def admin_review(license_id):
    if not session.get("is_admin"):
        return redirect(url_for("admin_login"))
    if db.get_pilot_license_by_id(license_id) is None:
        abort(404)
    status = request.form.get("status")
    if status in ("verified", "rejected", "pending"):
        db.review_pilot_license(license_id, status, (request.form.get("note") or "").strip()[:300])
    return redirect(url_for("admin_dashboard") + "#ausweise")


def admin_overview():
    return {
        "items": [dict(row) for row in db.list_pilot_licenses()],
        "kinds": LICENSE_KINDS,
        "status_labels": STATUS_LABELS,
    }
