"""Community-Forum der Bollisoft-Webseite (/forum).

- Lesen kann jeder, auch ohne Konto.
- Schreiben (Thema eroeffnen, antworten) nur mit Login und Benutzername
  (siehe konto.py). Eigene Beitraege kann man bearbeiten und loeschen.
- Der Admin (session["is_admin"], app.py's /admin/login) verwaltet die
  Ueberthemen (Kategorien, /forum/verwaltung), loescht beliebige Themen und
  Beitraege, sperrt/heftet Themen an und kann selbst schreiben - ohne eigenes
  Konto erscheint er als "Bollisoft-Team" (db.FORUM_ADMIN_AUTHOR).
- Beitraege sind reiner Text: HTML wird immer maskiert, nur Zeilenumbrueche
  und Links werden umgewandelt (siehe format_post()). Dazu bis zu 4 Bilder
  oder kurze Videos je Beitrag (community.py); neben jedem Beitrag steht eine
  Autoren-Karte mit Avatar, Titel, Status und Coptern.
"""

import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from markupsafe import Markup, escape

import community
import db
import roles
from i18n import gettext as _

# Erstausstattung der Kategorien (nur, solange die Tabelle leer ist).
DEFAULT_CATEGORIES = (
    ("allgemein", "Allgemein", "Alles rund um FPV, Fragen und Vorstellungen."),
    ("copter", "Copter & Builds", "Rahmen, Motoren, Setups und Build-Logs."),
    ("sender", "Sender & EdgeTX", "TX15, EdgeTX, Themes, Lua-Skripte und die Drohnen Tools."),
    ("betaflight", "Betaflight & Tuning", "PIDs, Filter, Rates und Blackbox-Auswertung."),
    ("pico", "FPV Gamification Pico", "Spielmodi, Plugins, Hardware und Hilfe."),
    ("offtopic", "Off-Topic", "Alles, was sonst nirgends passt."),
)
# Wuerden mit festen Unterseiten von /forum kollidieren.
RESERVED_SLUGS = {"thema", "beitrag", "verwaltung", "neu", "suche"}
THREADS_PER_PAGE = 20
SEARCH_PER_PAGE = 20
SEARCH_LIMIT = 500
SEARCH_TERM_MIN, SEARCH_TERMS_MAX, SEARCH_QUERY_MAX = 2, 8, 200
SNIPPET_WIDTH = 220
POSTS_PER_PAGE = 20
TITLE_MIN, TITLE_MAX = 4, 120
BODY_MIN, BODY_MAX = 2, 10000
CATEGORY_NAME_MAX, CATEGORY_DESCRIPTION_MAX = 60, 200
# Schreibbremse gegen Spam/Doppelklicks: Abstand zwischen zwei Beitraegen.
POST_INTERVAL = timedelta(seconds=15)
URL_RE = re.compile(r"https?://[^\s<>\"']+")

bp = Blueprint("forum", __name__, url_prefix="/forum")


# --- Darstellung ----------------------------------------------------------

def format_post(text):
    """Text -> sicheres HTML: alles maskiert, Absaetze, Zeilenumbrueche, Links."""
    paragraphs = []
    for block in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n").strip()):
        parts = []
        last = 0
        for match in URL_RE.finditer(block):
            url = match.group(0).rstrip(".,;:!?)")
            end = match.start() + len(url)
            parts.append(escape(block[last:match.start()]))
            parts.append(Markup('<a href="{0}" rel="nofollow ugc noopener" target="_blank">{0}</a>').format(url))
            last = end
        parts.append(escape(block[last:]))
        html = Markup("").join(parts).replace("\n", Markup("<br>"))
        paragraphs.append(Markup("<p>{}</p>").format(html))
    return Markup("\n").join(paragraphs)


def search_terms(query):
    """Suchbegriffe: Woerter oder "feste Wortfolgen", kleingeschrieben, ohne Dubletten."""
    terms = []
    for phrase, word in re.findall(r'"([^"]+)"|(\S+)', query or ""):
        term = " ".join((phrase or word).split()).lower()
        if len(term) >= SEARCH_TERM_MIN and term not in terms:
            terms.append(term)
    return terms[:SEARCH_TERMS_MAX]


def _search_pattern(terms):
    ordered = sorted(terms, key=len, reverse=True)
    return re.compile("|".join(re.escape(term) for term in ordered), re.IGNORECASE)


def highlight(text, pattern):
    """Text maskieren und Suchtreffer mit <mark> hervorheben."""
    parts, last = [], 0
    for match in pattern.finditer(text or ""):
        parts.append(escape(text[last:match.start()]))
        parts.append(Markup("<mark>{}</mark>").format(match.group(0)))
        last = match.end()
    parts.append(escape((text or "")[last:]))
    return Markup("").join(parts)


def snippet(body, pattern):
    """Ausschnitt rund um den ersten Treffer, Treffer hervorgehoben."""
    text = " ".join((body or "").split())
    match = pattern.search(text)
    start = max(0, (match.start() if match else 0) - SNIPPET_WIDTH // 3)
    if start:
        space = text.find(" ", start)
        start = space + 1 if 0 <= space < start + 20 else start
    end = min(len(text), start + SNIPPET_WIDTH)
    if end < len(text):
        space = text.rfind(" ", start, end)
        end = space if space > start + SNIPPET_WIDTH // 2 else end
    prefix = "… " if start else ""
    suffix = " …" if end < len(text) else ""
    return Markup("{}{}{}").format(prefix, highlight(text[start:end], pattern), suffix)


def format_time(value):
    """ISO (UTC) -> "21.10.2026 15:06" in Serverzeit."""
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%d.%m.%Y %H:%M")
    except ValueError:
        return value


def setup(app):
    """Template-Filter registrieren und die Kategorien einmalig anlegen."""
    app.add_template_filter(format_post, "forum_text")
    app.add_template_filter(format_time, "forum_time")
    if not db.forum_list_categories():
        for order, (slug, name, description) in enumerate(DEFAULT_CATEGORIES):
            db.forum_create_category(slug, name, description, (order + 1) * 10)


def _categories():
    """{slug: (Name, Beschreibung)} in Anzeigereihenfolge - die Standard-Kategorien uebersetzt."""
    return {row["slug"]: (_(row["name"]), _(row["description"])) for row in db.forum_list_categories()}


def _slugify(name):
    text = name.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-")


# --- Rechte ---------------------------------------------------------------

def _current_account():
    email = session.get("account_email")
    return db.get_account_by_email(email) if email else None


def _is_admin():
    return roles.is_admin()


def _is_mod():
    """Admin oder Moderator: darf Beitraege und Themen moderieren."""
    return roles.is_moderator()


def _can_edit(post, account):
    if _is_mod():
        return True
    return account is not None and post["author_email"].lower() == account["email"].lower()


def _write_gate(next_path):
    """(Autor-E-Mail, None) oder (None, Redirect). Der Admin schreibt mit
    seinem Konto, falls er auch als Benutzer eingeloggt ist, sonst als Team."""
    account = _current_account()
    if account is not None and account["username"]:
        return account["email"], None
    if _is_admin():
        return db.FORUM_ADMIN_AUTHOR, None
    if account is None:
        return None, redirect(url_for("login", next=next_path))
    return None, redirect(url_for("konto.profile", next=next_path, benutzername="fehlt"))


def _writer_name():
    """Anzeigename fuer "Antworten als …" - None, wenn nicht geschrieben werden darf."""
    account = _current_account()
    if account is not None and account["username"]:
        return account["username"]
    if _is_admin():
        return db.FORUM_TEAM_NAME
    return None


def _too_fast(email):
    if email == db.FORUM_ADMIN_AUTHOR:
        return False
    last = db.forum_last_post_time(email)
    if not last:
        return False
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(last) < POST_INTERVAL
    except ValueError:
        return False


def _check_text(title, body, with_title):
    if with_title and not TITLE_MIN <= len(title) <= TITLE_MAX:
        return _("Der Titel muss {min} bis {max} Zeichen lang sein.", min=TITLE_MIN, max=TITLE_MAX)
    if not BODY_MIN <= len(body) <= BODY_MAX:
        return _("Der Beitrag muss {min} bis {max} Zeichen lang sein.", min=BODY_MIN, max=BODY_MAX)
    return None


def _post_url(thread_id, post_id):
    page = db.forum_post_index(thread_id, post_id) // POSTS_PER_PAGE + 1
    return url_for("forum.thread", thread_id=thread_id, seite=page if page > 1 else None) + f"#beitrag-{post_id}"


def _page_count(total, per_page):
    return max(1, (total + per_page - 1) // per_page)


# --- Seiten ---------------------------------------------------------------

@bp.get("")
def index():
    return render_template(
        "forum_index.html",
        categories=_categories(),
        stats=db.forum_category_stats(),
        latest=db.forum_latest_threads(8),
        account=_current_account(),
        writer=_writer_name(),
        is_admin=_is_mod(),
        can_manage=_is_admin(),
    )


@bp.get("/suche")
def search():
    """Volltextsuche in Thementiteln und Beitraegen, optional je Ueberthema."""
    categories = _categories()
    query = " ".join((request.args.get("q") or "").split())[:SEARCH_QUERY_MAX]
    selected = request.args.get("kategorie") or ""
    if selected not in categories:
        selected = ""
    titles_only = request.args.get("nur") == "titel"
    terms = search_terms(query)
    results, total, page, pages = [], 0, 1, 1
    if terms:
        matches = db.forum_search_threads(terms, selected or None, titles_only, SEARCH_LIMIT)
        total = len(matches)
        pages = _page_count(total, SEARCH_PER_PAGE)
        page = min(max(1, request.args.get("seite", 1, type=int)), pages)
        chunk = matches[(page - 1) * SEARCH_PER_PAGE:page * SEARCH_PER_PAGE]
        ids = [match["id"] for match in chunk]
        threads = {row["id"]: row for row in db.forum_threads_by_ids(ids)}
        hits = {} if titles_only else db.forum_search_hits(ids, terms)
        pattern = _search_pattern(terms)
        for match in chunk:
            topic = threads.get(match["id"])
            if topic is None or topic["category"] not in categories:
                continue
            hit = hits.get(topic["id"])
            results.append({
                "thread": topic,
                "title": highlight(topic["title"], pattern),
                "title_hit": bool(match["title_hit"]),
                "hits": hit["hits"] if hit else 0,
                "snippet": snippet(hit["body"], pattern) if hit else None,
                "url": _post_url(topic["id"], hit["first_id"]) if hit else url_for("forum.thread", thread_id=topic["id"]),
            })
    return render_template(
        "forum_search.html",
        categories=categories,
        query=query,
        terms=terms,
        selected=selected,
        titles_only=titles_only,
        results=results,
        total=total,
        limit_reached=total >= SEARCH_LIMIT,
        page=page,
        pages=pages,
        too_short=bool(query) and not terms,
    )


@bp.get("/<category>")
def category(category):
    categories = _categories()
    if category not in categories:
        abort(404)
    total = db.forum_count_threads(category)
    pages = _page_count(total, THREADS_PER_PAGE)
    page = min(max(1, request.args.get("seite", 1, type=int)), pages)
    return render_template(
        "forum_category.html",
        categories=categories,
        slug=category,
        category=categories[category],
        threads=db.forum_list_threads(category, THREADS_PER_PAGE, (page - 1) * THREADS_PER_PAGE),
        page=page,
        pages=pages,
        account=_current_account(),
        writer=_writer_name(),
        is_admin=_is_mod(),
    )


@bp.route("/<category>/neu", methods=["GET", "POST"])
def new_thread(category):
    categories = _categories()
    if category not in categories:
        abort(404)
    author, gate = _write_gate(url_for("forum.new_thread", category=category))
    if gate:
        return gate
    error = None
    title = body = ""
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        body = (request.form.get("body") or "").strip()
        error = _check_text(title, body, with_title=True)
        if error is None and _too_fast(author):
            error = _("Bitte einen Moment warten, bevor du den nächsten Beitrag schreibst.")
        prepared = []
        if error is None:
            try:
                prepared = community.prepare_attachments(request.files.getlist("attachments"))
            except community.UploadError as exc:
                error = str(exc)
        if error is None:
            thread_id, post_id = db.forum_create_thread(category, title, author, body)
            community.store_attachments(post_id, prepared)
            return redirect(url_for("forum.thread", thread_id=thread_id))
    return render_template(
        "forum_form.html",
        mode="thread",
        slug=category,
        category=categories[category],
        title=title,
        body=body,
        error=error,
        writer=_writer_name(),
        limits=(TITLE_MAX, BODY_MAX),
    )


@bp.get("/thema/<int:thread_id>")
def thread(thread_id):
    categories = _categories()
    topic = db.forum_get_thread(thread_id)
    if topic is None or topic["category"] not in categories:
        abort(404)
    pages = _page_count(topic["post_count"], POSTS_PER_PAGE)
    page = min(max(1, request.args.get("seite", 1, type=int)), pages)
    account = _current_account()
    posts = db.forum_list_posts(thread_id, POSTS_PER_PAGE, (page - 1) * POSTS_PER_PAGE)
    return render_template(
        "forum_thread.html",
        attachments=db.forum_attachments_for(post["id"] for post in posts),
        cards=community.member_cards(post["author_email"] for post in posts),
        team_author=db.FORUM_ADMIN_AUTHOR,
        team=community.team_card() if any(post["author_is_team"] for post in posts) else None,
        categories=categories,
        topic=topic,
        slug=topic["category"],
        category=categories[topic["category"]],
        posts=posts,
        first_post_id=posts[0]["id"] if page == 1 and posts else None,
        page=page,
        pages=pages,
        account=account,
        writer=_writer_name(),
        is_admin=_is_mod(),
        can_edit=lambda post: _can_edit(post, account),
        owns_thread=_owns_thread(topic, account),
        body_max=BODY_MAX,
    )


@bp.post("/thema/<int:thread_id>/antworten")
def reply(thread_id):
    categories = _categories()
    topic = db.forum_get_thread(thread_id)
    if topic is None or topic["category"] not in categories:
        abort(404)
    author, gate = _write_gate(url_for("forum.thread", thread_id=thread_id))
    if gate:
        return gate
    body = (request.form.get("body") or "").strip()
    if topic["locked"] and not _is_mod():
        error = _("Dieses Thema ist gesperrt.")
    else:
        error = _check_text("", body, with_title=False)
    if error is None and _too_fast(author):
        error = _("Bitte einen Moment warten, bevor du den nächsten Beitrag schreibst.")
    prepared = []
    if error is None:
        try:
            prepared = community.prepare_attachments(request.files.getlist("attachments"))
        except community.UploadError as exc:
            error = str(exc)
    if error is not None:
        return render_template(
            "forum_form.html",
            mode="reply",
            slug=topic["category"],
            category=categories[topic["category"]],
            topic=topic,
            body=body,
            error=error,
            writer=_writer_name(),
            limits=(TITLE_MAX, BODY_MAX),
        )
    post_id = db.forum_add_post(thread_id, author, body)
    community.store_attachments(post_id, prepared)
    return redirect(_post_url(thread_id, post_id))


@bp.route("/beitrag/<int:post_id>/bearbeiten", methods=["GET", "POST"])
def edit_post(post_id):
    post = db.forum_get_post(post_id)
    if post is None:
        abort(404)
    account = _current_account()
    if not _can_edit(post, account):
        abort(403)
    title = post["title"]
    body = post["body"]
    error = None
    existing = db.forum_attachments_for([post_id]).get(post_id, [])
    if request.method == "POST":
        body = (request.form.get("body") or "").strip()
        if post["is_first"]:
            title = (request.form.get("title") or "").strip()
        error = _check_text(title, body, with_title=bool(post["is_first"]))
        remove = {a["id"] for a in existing if str(a["id"]) in request.form.getlist("remove_attachment")}
        prepared = []
        if error is None:
            try:
                prepared = community.prepare_attachments(request.files.getlist("attachments"), existing=len(existing) - len(remove))
            except community.UploadError as exc:
                error = str(exc)
        if error is None:
            db.forum_update_post(post_id, body, title if post["is_first"] else None)
            community.remove_attachment_files(db.forum_delete_attachments(remove, post_id))
            community.store_attachments(post_id, prepared)
            return redirect(_post_url(post["thread_id"], post_id))
    return render_template(
        "forum_form.html",
        mode="edit",
        slug=post["category"],
        category=_categories().get(post["category"], ("Forum", "")),
        post=post,
        attachments=existing,
        title=title,
        body=body,
        error=error,
        writer=_writer_name(),
        limits=(TITLE_MAX, BODY_MAX),
    )


@bp.post("/beitrag/<int:post_id>/loeschen")
def delete_post(post_id):
    post = db.forum_get_post(post_id)
    if post is None:
        abort(404)
    if not _can_edit(post, _current_account()):
        abort(403)
    # Loescht der Autor seinen Eroeffnungsbeitrag, verschwindet das ganze Thema
    # (die Rueckfrage im Browser nennt die Zahl der Beitraege).
    thread_id, thread_deleted = db.forum_delete_post(post_id)
    community.cleanup_attachments()
    if thread_deleted:
        return redirect(url_for("forum.category", category=post["category"], geloescht=1))
    return redirect(url_for("forum.thread", thread_id=thread_id))


def _owns_thread(topic, account):
    return account is not None and topic["author_email"].lower() == account["email"].lower()


@bp.post("/thema/<int:thread_id>/loeschen")
def delete_thread(thread_id):
    """Ganzes Thema loeschen - fuer den Ersteller des Themas und den Admin."""
    topic = db.forum_get_thread(thread_id)
    if topic is None:
        abort(404)
    if not (_is_mod() or _owns_thread(topic, _current_account())):
        abort(403)
    category = db.forum_delete_thread(thread_id)
    community.cleanup_attachments()
    return redirect(url_for("forum.category", category=category, geloescht=1))


# --- Admin ----------------------------------------------------------------

def _mod_only():
    if not _is_mod():
        abort(403)


@bp.post("/thema/<int:thread_id>/moderation")
def moderate(thread_id):
    _mod_only()
    topic = db.forum_get_thread(thread_id)
    if topic is None:
        abort(404)
    action = request.form.get("action")
    if action == "lock":
        db.forum_set_thread_flag(thread_id, "locked", not topic["locked"])
    elif action == "pin":
        db.forum_set_thread_flag(thread_id, "pinned", not topic["pinned"])
    elif action == "delete":
        category = db.forum_delete_thread(thread_id)
        community.cleanup_attachments()
        return redirect(url_for("forum.category", category=category, geloescht=1))
    elif action == "move":
        target = request.form.get("category")
        if target in _categories() and target != topic["category"]:
            db.forum_move_thread(thread_id, target)
            # Zurueck zur Liste, aus der verschoben wurde (nur Forum-Pfade).
            back = request.form.get("next") or ""
            if back.startswith("/forum") and not back.startswith("//"):
                return redirect(back + ("&" if "?" in back else "?") + f"verschoben={thread_id}")
            return redirect(url_for("forum.thread", thread_id=thread_id, verschoben=1))
    return redirect(url_for("forum.thread", thread_id=thread_id))


def _category_fields(form):
    name = (form.get("name") or "").strip()
    description = (form.get("description") or "").strip()
    try:
        sort_order = int(form.get("sort_order") or 0)
    except ValueError:
        sort_order = 0
    if not 2 <= len(name) <= CATEGORY_NAME_MAX:
        return None, _("Der Name muss 2 bis {max} Zeichen lang sein.", max=CATEGORY_NAME_MAX)
    if len(description) > CATEGORY_DESCRIPTION_MAX:
        return None, _("Die Beschreibung darf höchstens {category_description_max} Zeichen lang sein.", category_description_max=CATEGORY_DESCRIPTION_MAX)
    return {"name": name, "description": description, "sort_order": sort_order}, None


@bp.route("/verwaltung", methods=["GET", "POST"])
def manage():
    """Ueberthemen (Kategorien) anlegen, bearbeiten, sortieren und loeschen."""
    if not _is_admin():
        return redirect(url_for("admin_login"))
    error = None
    message = request.args.get("ok")
    if request.method == "POST":
        action = request.form.get("action")
        slug = request.form.get("slug") or ""
        if action == "delete":
            if db.forum_get_category(slug) is not None:
                db.forum_delete_category(slug)
                community.cleanup_attachments()
                return redirect(url_for("forum.manage", ok="geloescht"))
        else:
            fields, error = _category_fields(request.form)
            if fields and action == "update" and db.forum_get_category(slug) is not None:
                db.forum_update_category(slug, fields["name"], fields["description"], fields["sort_order"])
                return redirect(url_for("forum.manage", ok="gespeichert"))
            if fields and action == "create":
                new_slug = _slugify(fields["name"]) or "kategorie"
                if new_slug in RESERVED_SLUGS:
                    new_slug += "-forum"
                base, number = new_slug, 2
                while db.forum_get_category(new_slug) is not None:
                    new_slug = f"{base}-{number}"
                    number += 1
                try:
                    db.forum_create_category(new_slug, fields["name"], fields["description"], fields["sort_order"])
                except sqlite3.IntegrityError:
                    error = _("Diese Kategorie gibt es bereits.")
                else:
                    return redirect(url_for("forum.manage", ok="angelegt"))
    stats = db.forum_category_stats()
    return render_template(
        "forum_manage.html",
        categories=db.forum_list_categories(),
        stats=stats,
        error=error,
        message=message,
        limits=(CATEGORY_NAME_MAX, CATEGORY_DESCRIPTION_MAX),
    )
