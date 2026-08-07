"""koth - King of the Hill-Spielmodus als Plugin-Huelle um koth_mode.py's
BLE-Spiellogik.

Wie race/infection bleibt die eigentliche BLE-Spiellogik (KothMode-Klasse,
Advertise/Scan-Protokoll, handle_koth_route()) bewusst in
source/koth_mode.py, also fest Teil der Firmware - dieses Modul ist nur
eine duenne Plugin-Huelle darum: erzeugt/startet die eine KothMode-Instanz
inkl. ihres eigenstaendigen asyncio-Tasks (KothMode.run()) und stellt die
Web-/Dashboard-/Zuschauer-Ansicht-Integration (Routen, ui_slots, ui_pages)
bereit, die frueher fest in gmr.py verdrahtet war. Referenz fuer dieses
Muster (Firmware-Logik + duenne Plugin-Huelle) siehe template/README.md.

Lifecycle: setup() erstellt einmalig eine KothMode-Instanz und startet
deren run()-Task (Dauerschleife mit BLE-Scan/Punkte-Timing) - ein erneutes
Aktivieren nach dem Deaktivieren startet nur einen neuen Task fuer dieselbe
Instanz, siehe teardown(). Kein loop() noetig: KothMode treibt sich ueber
ihren eigenen Task selbst, nicht ueber plugin_manager.run_loops()'s
Intervall-Tick.

Nach diesem Umzug bleibt "/gamemodes-view" als generische, oeffentliche
Zuschauer-Seite bestehen - gmr.py liefert sie weiterhin aus (kein Plugin-
spezifischer Inhalt mehr dort ausser ueber die gamemodes_*-ui_slots), dieses
Plugin traegt nur noch seine eigene Karte via render_gamemodes_card_slot()
bei (siehe dortiger Marker in gamemodes_view.html).
"""

import asyncio

from koth_mode import KothMode, handle_koth_route as _handle_koth_route

try:
    from main import DEFAULT_PILOT_NAME
except Exception:
    DEFAULT_PILOT_NAME = ""

try:
    from main import send_html_file as _send_html_file
except Exception:
    _send_html_file = None

ADMIN_KOTH_HTML_PATH = "mods/koth/admin_koth.html"


def get_ui_schema():
    """Native UI-Beschreibung fuer die Android-App (siehe manifest.json's
    "ui_pages" und plugin_manager.get_ui_schema()) - inhaltliches Pendant zu
    admin_koth.html. "role" wird bewusst NICHT aufgenommen (nur ueber
    index_gatehill.html waehlbar, siehe koth_mode.py's _normalize_config()-
    Kommentar), analog zu race's Ausschluss von "role"."""
    return {
        "title": "King of the Hill",
        "poll_endpoint": "/koth-data",
        "poll_interval_ms": 1000,
        "sections": [
            {
                "type": "stats",
                "title": "Live-Status",
                "fields": [
                    {"key": "running", "label": "Zustand", "kind": "bool_text", "true_text": "Aktiv", "false_text": "Inaktiv"},
                    {"key": "remaining_seconds", "label": "Restzeit (Sek.)", "kind": "text"},
                    {"key": "last_event", "label": "Letztes Ereignis", "kind": "text"},
                    {"key": "last_rssi", "label": "Signalstaerke", "kind": "text"},
                    {"key": "in_range", "label": "Empfang zum Huegel", "kind": "bool_text", "true_text": "Ja", "false_text": "Nein"},
                    {"key": "score", "label": "Punktestand", "kind": "text"},
                    {"key": "node_id", "label": "Node ID", "kind": "text"},
                ],
            },
            {
                "type": "form",
                "title": "Rundeneinstellungen",
                "submit_endpoint": "/koth-config",
                "submit_label": "Speichern & Runde starten",
                "hint": (
                    "Ein Pico ist der Huegel und sendet einen BLE-Beacon. Alle "
                    "Spieler-Picos sammeln Punkte, solange sie Empfang zum Huegel "
                    "haben (RSSI ueber der Naeheschwelle). Die Huegel-Rolle ist nur "
                    "auf einem als Gate/Hill Pico eingerichteten Geraet waehlbar."
                ),
                "fields": [
                    {"key": "enabled", "label": "Modus aktivieren", "kind": "toggle"},
                    {"key": "round_seconds", "label": "Rundendauer (Sek.)", "kind": "number", "min": 30, "max": 3600},
                    {"key": "rssi_threshold", "label": "Naeheschwelle RSSI (dBm)", "kind": "number", "min": -95, "max": -20},
                    {"key": "points_per_second", "label": "Punkte pro Sekunde", "kind": "number", "min": 0.1, "max": 100},
                ],
            },
            {
                "type": "actions",
                "buttons": [
                    {"label": "Runde stoppen", "endpoint": "/koth-stop", "style": "muted"},
                ],
            },
            {
                "type": "list",
                "title": "Bestenliste",
                "source_key": "leaderboard",
                "item_label_key": "name",
                "item_label_prefix": None,
                "item_value_key": "score",
                "empty_text": "Noch keine Spieler erkannt.",
            },
        ],
    }


def render_dashboard_nav_slot():
    """ui_slots-Ziel "dashboard_nav" - siehe source/mods/shooter/main.py's
    render_dashboard_nav_slot()-Docstring fuer die Begruendung des generischen
    Mechanismus."""
    return '<a href="/admin-koth" data-i18n="nav.koth">King of the Hill</a>'


def render_dashboard_card_slot():
    """ui_slots-Ziel "dashboard_card" - Dashboard-Kachel fuer
    admin_dashboard.html's <!--PLUGIN_SLOT:dashboard_card--> Marker (gleiches
    Markup wie zuvor fest verdrahtet, jetzt nur noch bei aktivem Plugin;
    ".card.koth" bleibt als CSS-Klasse im Kern-Template erhalten)."""
    return (
        '<a class="card koth" href="/admin-koth">'
        '<h3 data-i18n="dashboard.card.kothTitle">&#128081; King of the Hill</h3>'
        '<p data-i18n="dashboard.card.kothText">Punkte sammeln, solange Empfang zum Huegel-Pico besteht</p>'
        '</a>'
    )


def render_dashboard_stat_slot():
    """ui_slots-Ziel "dashboard_stat" - Statistikkachel fuer
    admin_dashboard.html's <!--PLUGIN_SLOT:dashboard_stat--> Marker. Werte
    werden von render_dashboard_script_slot()'s eigenem DASHBOARD_HOOKS-
    Eintrag befuellt, NICHT mehr vom Dashboard-Kern-Skript."""
    return (
        '<div class="stile" style="--sc:#e6a23c"><div class="sticon">&#128081;</div>'
        '<div class="stbody"><div class="stlabel" data-i18n="dashboard.statKothLabel">King of the Hill</div>'
        '<div class="stval" id="st_koth_val">-</div><div class="stsub" id="st_koth_sub"></div></div></div>'
    )


def render_dashboard_script_slot():
    """ui_slots-Ziel "dashboard_script" - eigenstaendiges <script>-Fragment
    fuer admin_dashboard.html's <!--PLUGIN_SLOT:dashboard_script--> Marker,
    siehe source/mods/shooter/main.py's render_dashboard_script_slot()-
    Docstring fuer die Erklaerung des Musters (DASHBOARD_HOOKS)."""
    return """<script>
(function(){
window.DASHBOARD_HOOKS=window.DASHBOARD_HOOKS||[];
window.DASHBOARD_HOOKS.push(function(){
return fetch('/koth-log',{cache:'no-store'}).then(function(r){return r.json();}).then(function(d){
var log=d.log||[];
var tr=window.t||function(k,f){return f;};
var valEl=document.getElementById('st_koth_val'),subEl=document.getElementById('st_koth_sub');
if(log.length){
var best=log.reduce(function(a,b){return (b.score||0)>(a.score||0)?b:a;},log[0]);
if(valEl)valEl.innerText=(best.score||0)+' Pkt';
if(subEl)subEl.innerText=log.length+' '+tr('dashboard.rounds','Runden');
}else{
if(valEl)valEl.innerText=tr('dashboard.noRounds','Noch keine Runde');
if(subEl)subEl.innerText='';
}
return log.map(function(e){
return {ts:e.ts_s||0,time:e.timestamp||'',color:'#e6a23c',text:'&#128081; '+tr('dashboard.actKoth','KOTH-Runde beendet')+': '+(e.score||0)+' Pkt'};
});
}).catch(function(){return [];});
});
})();
</script>"""


def render_gamemodes_button_slot():
    """ui_slots-Ziel "gamemodes_button" - Steuer-Button fuer die oeffentliche
    Zuschauer-Ansicht gamemodes_view.html's <!--PLUGIN_SLOT:gamemodes_button-->
    Marker."""
    return '<a class="b" href="/admin-koth" data-i18n="gamemodesView.controlKothButton">&#128081; King of the Hill steuern</a>'


def render_gamemodes_card_slot():
    """ui_slots-Ziel "gamemodes_card" - Live-Status-Karte fuer
    gamemodes_view.html's <!--PLUGIN_SLOT:gamemodes_card--> Marker (gleiches
    Markup wie zuvor dort fest verdrahtet, jetzt nur noch bei aktivem Plugin
    eingeblendet - siehe source/mods/shooter/main.py's
    render_gamemodes_card_slot()-Docstring)."""
    return (
        '<div class="game" style="--gc:var(--koth)">'
        '<h2><span class="dot" id="k_dot"></span> <span data-i18n="gamemodesView.kothSection">&#128081; King of the Hill</span></h2>'
        '<div class="grid">'
        '<div class="st"><span data-i18n="koth.state">Zustand</span><b id="k_role">-</b></div>'
        '<div class="st"><span data-i18n="koth.remaining">Restzeit</span><b id="k_remaining">-</b></div>'
        '<div class="st"><span data-i18n="koth.event">Letztes Ereignis</span><b id="k_event">-</b></div>'
        '<div class="st"><span data-i18n="koth.rssi">Signalstaerke</span><b id="k_rssi">-</b></div>'
        '<div class="st"><span data-i18n="koth.inRange">Empfang zum Huegel</span><b id="k_inrange">-</b></div>'
        '<div class="st"><span data-i18n="koth.score">Punktestand</span><b id="k_score">0</b></div>'
        '</div>'
        '<div class="hint" data-i18n="koth.leaderboardTitle">Bestenliste</div>'
        '<div id="k_leaderboard"></div>'
        '</div>'
    )


def render_gamemodes_script_slot():
    """ui_slots-Ziel "gamemodes_script" - eigenstaendiges <script>-Fragment
    fuer gamemodes_view.html's <!--PLUGIN_SLOT:gamemodes_script--> Marker,
    registriert sich selbst in window.GAMEMODES_HOOKS - gleiches Muster wie
    source/mods/shooter/main.py's render_gamemodes_script_slot()."""
    return """<script>
(function(){
window.GAMEMODES_HOOKS=window.GAMEMODES_HOOKS||[];
function tr(k,f){return (window.t||function(_k,fallback){return fallback;})(k,f);}
function fmt(sec){sec=Math.max(0,Number(sec)||0);var m=Math.floor(sec/60),s=sec%60;return m+':'+String(s).padStart(2,'0');}
function showLeaderboard(d){
var list=document.getElementById('k_leaderboard'),entries=Array.isArray(d.leaderboard)?d.leaderboard:[];
list.replaceChildren();
if(!entries.length){var row=document.createElement('div');row.className='hint';row.innerText=tr('koth.noPlayers','Noch keine Spieler erkannt.');list.appendChild(row);return;}
entries.forEach(function(e,i){var row=document.createElement('div');row.className='lb';var label=document.createElement('span');label.innerText=(i+1)+'. '+(e.name||('node-'+e.id));var score=document.createElement('b');score.innerText=e.score;row.append(label,score);list.appendChild(row);});
}
function update(d){
var running=!!d.running,role=running?d.role:'inactive';
document.getElementById('k_dot').classList.toggle('on',running);
document.getElementById('k_role').innerText=role==='hill'?tr('koth.roleHill','Huegel'):role==='player'?tr('koth.rolePlayer','Spieler'):tr('koth.inactive','Inaktiv');
document.getElementById('k_remaining').innerText=fmt(d.remaining_seconds);
document.getElementById('k_event').innerText=d.last_event||'-';
document.getElementById('k_rssi').innerText=(d.last_rssi===null||d.last_rssi===undefined)?'-':d.last_rssi+' dBm';
document.getElementById('k_inrange').innerText=d.in_range?tr('common.yes','Ja'):tr('common.no','Nein');
document.getElementById('k_score').innerText=d.score||0;
showLeaderboard(d);
}
function poll(){fetch('/koth-data',{cache:'no-store'}).then(function(r){return r.json();}).then(update).catch(function(){}).finally(function(){setTimeout(poll,1000);});}
window.GAMEMODES_HOOKS.push(poll);
})();
</script>"""


# ==================== Plugin-Lifecycle (siehe plugin_manager.py) ====================

_manager = None
_task = None


def setup(context):
    """Erzeugt einmalig die KothMode-Instanz und startet ihren eigenen
    Dauerlauf-Task (BLE-Scan/Punkte-Timing) - ein Deaktivieren+Aktivieren
    erzeugt bewusst KEINE neue Instanz, nur einen neuen Task fuer dieselbe
    Instanz (siehe teardown())."""
    global _manager, _task
    if _manager is None:
        _manager = KothMode(DEFAULT_PILOT_NAME, context["debug_log"])
    if _task is None:
        _task = asyncio.create_task(_manager.run())
    context["debug_log"]("[koth] Plugin aktiviert (node_id=%s)" % _manager.node_id)


def teardown():
    """Stoppt den Dauerlauf-Task und eine laufende Runde - die KothMode-
    Instanz selbst bleibt bestehen (siehe setup())."""
    global _task
    if _task is not None:
        try:
            _task.cancel()
        except Exception:
            pass
        _task = None
    if _manager is not None:
        try:
            _manager.stop_round("Plugin deaktiviert")
        except Exception:
            pass


async def handle_route(writer, request_path, request_method, query_params, body_params):
    if request_path == "/admin-koth":
        try:
            import pico_web_api
            await pico_web_api.send_admin_html_with_slot(writer, ADMIN_KOTH_HTML_PATH, "dashboard_nav")
        except ImportError:
            if _send_html_file is not None:
                await _send_html_file(writer, ADMIN_KOTH_HTML_PATH)
        return True

    if request_path.startswith("/koth-") and _manager is not None:
        return await _handle_koth_route(writer, request_path, request_method, query_params, body_params, _manager)

    return False
