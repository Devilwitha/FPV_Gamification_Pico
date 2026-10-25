"""Startseite "/": die Drohnen Tools als Hauptseite von Bollisoft.

Die Werkzeuge selbst liegen weiter als eigenstaendige Seiten unter
/tx15-werkstatt/ (Ordner drohnen-tools/), der Blackbox Viewer unter
/blackbox-viewer/. Hier entsteht nur die Uebersicht: Werkzeuge nach Gruppen,
Einstiegswege, Neues aus Forum und Leaderboard. Welche Drohnen-Werkzeuge
sichtbar sind, steuert weiterhin drohnen-tools/config.js.
"""

import re
from pathlib import Path

import db
import forum
from i18n import gettext as _

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_JS = PROJECT_ROOT / "drohnen-tools" / "config.js"
TOOLS_URL = "/tx15-werkstatt/"

# Gruppen in der Reihenfolge, in der man eine Drohne baut und fliegt
GROUPS = (
    ("bauen", "Planen & bauen", "Teile zusammenstellen, Gewicht und Flugzeit abschätzen, die Verkabelung planen."),
    ("drohne", "Drohne einrichten", "Flugcontroller, Regler und Empfänger per USB konfigurieren und flashen."),
    ("sender", "Sender (EdgeTX)", "Für die RadioMaster TX15 und andere EdgeTX-Sender mit Farbdisplay."),
    ("fliegen", "Fliegen & auswerten", "Blackbox-Logs auswerten, Tricks erkennen und mit anderen vergleichen."),
)

# key, Gruppe, Name, Kurzbeschreibung, Link, Schlagworte, Schalter in config.js (None = immer)
TOOLS = (
    ("build", "bauen", "Build Studio", "Teileliste mit Gewicht, Preis, Schub und Flugzeit – dazu der Schaltplan mit Prüfung und Betaflight-Befehlen.",
     TOOLS_URL + "build-studio.html", ("Teileliste", "Schaltplan", "CLI"), None),
    ("betaflight", "drohne", "Betaflight Configurator", "Flugcontroller einrichten: Ports, Empfänger, Modi, PIDs und OSD, dazu Firmware flashen.",
     TOOLS_URL + "betaflight.html", ("USB", "MSP", "DFU"), "betaflight"),
    ("esc", "drohne", "ESC Configurator", "Regler mit BLHeli_S, Bluejay oder AM32 einstellen und flashen – alle vier auf einmal.",
     TOOLS_URL + "esc-configurator.html", ("BLHeli_S", "Bluejay", "AM32"), "escconfigurator"),
    ("elrs", "drohne", "ELRS Flasher", "ExpressLRS flashen: Firmware wählen, Binding-Phrase und Region einstellen, per USB oder WLAN.",
     TOOLS_URL + "elrs-flasher.html", ("ExpressLRS", "Passthrough", "WLAN"), "webflasher"),
    ("theme", "sender", "Theme Studio", "Farbschemata für EdgeTX gestalten – mit Live-Vorschau des Displays, direkt auf die SD-Karte.",
     TOOLS_URL + "theme-studio.html", ("Themes", "Live-Vorschau"), None),
    ("lua", "sender", "Lua Studio", "Widgets und Tools zeichnen, Funktions- und Mischer-Skripte aus Vorlagen erzeugen.",
     TOOLS_URL + "lua-studio.html", ("Widgets", "Tools", "Skripte"), None),
    ("backup", "sender", "Backup Studio", "SD-Karte sichern und gezielt zurückspielen, Firmware installieren, doppelte Tools aufräumen.",
     TOOLS_URL + "backup-studio.html", ("Backup", "Firmware", "SD-Karte"), None),
    ("blackbox", "fliegen", "Blackbox Viewer", "Blackbox-Logs auswerten: Tricks erkennen und bepunkten, Video mit HUD-Overlay synchronisieren.",
     "/blackbox-viewer/", (".bbl", "Tricks", "HUD"), "blackbox"),
    ("leaderboard", "fliegen", "Leaderboard", "Die besten Trick-Flüge mit Video – weltweit, nach Land und nach Akku-Klasse.",
     "/leaderboard", ("Rangliste", "Video", "1S–8S"), None),
)

# "Wo fange ich an?": Wege durch die Werkzeuge
PATHS = (
    ("Neue Drohne bauen", "Teile planen, verkabeln, einrichten.", ("build", "betaflight", "esc", "elrs")),
    ("Sender einrichten", "Sichern, gestalten, Skripte aufspielen.", ("backup", "theme", "lua")),
    ("Flug auswerten", "Log laden, Tricks sehen, vergleichen.", ("blackbox", "leaderboard")),
)


def _features():
    """Schalter aus drohnen-tools/config.js (z. B. webflasher: true)."""
    try:
        text = CONFIG_JS.read_text(encoding="utf-8")
    except OSError:
        return {}
    return {key: value == "true" for key, value in re.findall(r"^\s*(\w+)\s*:\s*(true|false)", text, re.MULTILINE)}


def home_context():
    features = _features()
    tools = [
        {"key": key, "group": group, "name": name, "desc": _(desc), "href": href, "tags": tags}
        for key, group, name, desc, href, tags, flag in TOOLS
        if flag is None or features.get(flag, True)
    ]
    by_key = {tool["key"]: tool for tool in tools}
    groups = [
        {"key": key, "name": _(name), "desc": _(desc), "tools": [t for t in tools if t["group"] == key]}
        for key, name, desc in GROUPS
    ]
    paths = [
        {"name": _(name), "desc": _(desc), "steps": [by_key[k] for k in keys if k in by_key]}
        for name, desc, keys in PATHS
    ]
    forum_posts = sum(posts for _threads, posts, _last in db.forum_category_stats().values())
    top = db.leaderboard_list(5)
    return {
        "groups": [g for g in groups if g["tools"]],
        "paths": [p for p in paths if p["steps"]],
        "tool_count": len(tools),
        "stats": {"pilots": db.count_accounts(), "posts": forum_posts, "flights": db.leaderboard_count()},
        # angepinnte Themen (z. B. Forenregeln) stehen im Forum ohnehin oben
        "latest_threads": [row for row in db.forum_latest_threads(20) if not row["pinned"]][:5],
        "categories": {slug: name for slug, (name, _desc) in forum._categories().items()},
        "top_entries": top,
        "leader": top[0] if top else None,
    }
