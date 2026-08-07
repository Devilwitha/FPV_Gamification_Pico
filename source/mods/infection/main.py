"""infection - Infektions-Spielmodus als Plugin-Huelle um infection_mode.py's
BLE-Spiellogik.

Wie race/koth bleibt die eigentliche BLE-Spiellogik (InfectionMode-Klasse,
Advertise/Scan-/Lobby-Protokoll, handle_infection_route()) bewusst in
source/infection_mode.py, also fest Teil der Firmware - dieses Modul ist nur
eine duenne Plugin-Huelle darum: erzeugt/startet die eine InfectionMode-
Instanz inkl. ihres eigenstaendigen asyncio-Tasks (InfectionMode.run()) und
stellt die Web-/Dashboard-Integration (Routen, ui_slots, ui_pages) bereit,
die frueher fest in main.py verdrahtet war. Anders als Race/KOTH hat
Infection KEINE eigene Zuschauer-Seite in gamemodes_view.html, sondern eine
eigenstaendige Seite (/infection-view, siehe infection_view.html) - daher
gibt es hier auch keine gamemodes_*-ui_slots.

Lifecycle: setup() erstellt einmalig eine InfectionMode-Instanz und startet
deren run()-Task (Dauerschleife mit BLE-Scan/Lobby/Runden-Timing) - ein
erneutes Aktivieren nach dem Deaktivieren startet nur einen neuen Task fuer
dieselbe Instanz, siehe teardown(). Kein loop() noetig: InfectionMode treibt
sich ueber ihren eigenen Task selbst.

WICHTIG (RAM-Fragmentierung): main.py laedt dieses Plugin explizit VOR AP-/
HTTP-Server-Start via plugin_manager.load_single_plugin("infection") (siehe
main.py's main_async()) - das Kompilieren von infection_mode.py schlug auf
dem Pico W NACH AP+Socket-Start schon bei einer kleinen Allokation (2344
Bytes) mit "memory allocation failed" fehl. Dieses Timing NICHT aendern.

get_status()/get_session_summary_text() werden von main.py generisch ueber
plugin_manager.get_plugin_module("infection") abgefragt (fuer /data's
"infection_status" bzw. den Arcade-Sitzungs-Export), damit main.py nicht
fest von diesem Plugin abhaengen muss.
"""

import asyncio

from infection_mode import InfectionMode, handle_infection_route as _handle_infection_route

try:
    from main import AP_SSID, AP_PASSWORD, DEFAULT_PILOT_NAME
except Exception:
    AP_SSID = ""
    AP_PASSWORD = ""
    DEFAULT_PILOT_NAME = ""

try:
    from main import send_html_file as _send_html_file
except Exception:
    _send_html_file = None

ADMIN_INFECTION_HTML_PATH = "mods/infection/admin_infection.html"
INFECTION_VIEW_HTML_PATH = "mods/infection/infection_view.html"


def get_ui_schema():
    """Native UI-Beschreibung fuer die Android-App (siehe manifest.json's
    "ui_pages" und plugin_manager.get_ui_schema()) - inhaltliches Pendant zu
    admin_infection.html. "initial_role"/"game_mode" (Auswahlfelder) werden
    bewusst NICHT aufgenommen, da get_ui_schema() nur "toggle"/"number" kennt
    (siehe source/mods/shooter/main.py's get_ui_schema() fuer denselben
    Vorbehalt) - beide bleiben ueber die Web-Admin-Seite konfigurierbar."""
    return {
        "title": "Infection",
        "poll_endpoint": "/infection-data",
        "poll_interval_ms": 1000,
        "sections": [
            {
                "type": "stats",
                "title": "Live-Status",
                "fields": [
                    {"key": "running", "label": "Zustand", "kind": "bool_text", "true_text": "Aktiv", "false_text": "Inaktiv"},
                    {"key": "role", "label": "Rolle", "kind": "text"},
                    {"key": "infected", "label": "Infiziert", "kind": "bool_text", "true_text": "Ja", "false_text": "Nein"},
                    {"key": "remaining_seconds", "label": "Restzeit (Sek.)", "kind": "text"},
                    {"key": "last_event", "label": "Letztes Ereignis", "kind": "text"},
                    {"key": "infection_count", "label": "Infektionen", "kind": "text"},
                    {"key": "node_id", "label": "Node ID", "kind": "text"},
                ],
            },
            {
                "type": "form",
                "title": "Infection-Einstellungen",
                "submit_endpoint": "/infection-config",
                "submit_label": "Speichern & Starten",
                "hint": (
                    "Rolle und Spielmodus koennen nur ueber die Web-Admin-Seite "
                    "gewaehlt werden (Auswahlfelder werden hier nicht unterstuetzt)."
                ),
                "fields": [
                    {"key": "enabled", "label": "Modus aktivieren", "kind": "toggle"},
                    {"key": "round_seconds", "label": "Rundendauer (Sek.)", "kind": "number", "min": 30, "max": 3600},
                    {"key": "rssi_threshold", "label": "Naeheschwelle RSSI (dBm)", "kind": "number", "min": -95, "max": -20},
                    {"key": "cooldown_seconds", "label": "Sperrzeit (Sek.)", "kind": "number", "min": 1, "max": 60},
                ],
            },
            {
                "type": "actions",
                "buttons": [
                    {"label": "Runde stoppen", "endpoint": "/infection-stop", "style": "muted"},
                ],
            },
            {
                "type": "list",
                "title": "Kontakte",
                "source_key": "contacts",
                "item_label_key": None,
                "item_label_prefix": "Kontakt",
                "item_value_key": None,
                "empty_text": "Noch keine Kontakte registriert.",
            },
        ],
    }


def render_dashboard_nav_slot():
    """ui_slots-Ziel "dashboard_nav" - siehe source/mods/shooter/main.py's
    render_dashboard_nav_slot()-Docstring fuer die Begruendung des generischen
    Mechanismus."""
    return '<a href="/admin-infection" data-i18n="nav.infection">Infection</a>'


def render_dashboard_card_slot():
    """ui_slots-Ziel "dashboard_card" - Dashboard-Kachel fuer
    admin_dashboard.html's <!--PLUGIN_SLOT:dashboard_card--> Marker (gleiches
    Markup wie zuvor fest verdrahtet, jetzt nur noch bei aktivem Plugin)."""
    return (
        '<a class="card" href="/admin-infection">'
        '<h3 data-i18n="dashboard.card.infectionTitle">Infection Modus</h3>'
        '<p data-i18n="dashboard.card.infectionText">Zwei Picos als Infizierter und Sucher spielen</p>'
        '</a>'
    )


def render_dashboard_stat_slot():
    """ui_slots-Ziel "dashboard_stat" - Statistikkachel fuer
    admin_dashboard.html's <!--PLUGIN_SLOT:dashboard_stat--> Marker. Werte
    werden von render_dashboard_script_slot()'s eigenem DASHBOARD_HOOKS-
    Eintrag befuellt, NICHT mehr vom Dashboard-Kern-Skript."""
    return (
        '<div class="stile" style="--sc:#d4473f"><div class="sticon">&#9763;&#65039;</div>'
        '<div class="stbody"><div class="stlabel" data-i18n="dashboard.statInfectionLabel">Infection</div>'
        '<div class="stval" id="st_infection_val">-</div><div class="stsub" id="st_infection_sub"></div></div></div>'
    )


def render_dashboard_script_slot():
    """ui_slots-Ziel "dashboard_script" - eigenstaendiges <script>-Fragment
    fuer admin_dashboard.html's <!--PLUGIN_SLOT:dashboard_script--> Marker,
    siehe source/mods/shooter/main.py's render_dashboard_script_slot()-
    Docstring fuer die Erklaerung des Musters (DASHBOARD_HOOKS)."""
    return """<script>
(function(){
window.DASHBOARD_HOOKS=window.DASHBOARD_HOOKS||[];
function resultLabel(r){
var tr=window.t||function(_k,f){return f;};
if(r==='won')return tr('dashboard.resultWon','Sieg');
if(r==='lost')return tr('dashboard.resultLost','Niederlage');
if(r==='stopped')return tr('dashboard.resultStopped','Abgebrochen');
return r||'';
}
window.DASHBOARD_HOOKS.push(function(){
return fetch('/infection-log',{cache:'no-store'}).then(function(r){return r.json();}).then(function(d){
var log=d.log||[];
var tr=window.t||function(k,f){return f;};
var valEl=document.getElementById('st_infection_val'),subEl=document.getElementById('st_infection_sub');
if(log.length){
var wins=log.filter(function(e){return e.result==='won';}).length;
if(valEl)valEl.innerText=wins+'/'+log.length;
if(subEl)subEl.innerText=tr('dashboard.wins','Siege');
}else{
if(valEl)valEl.innerText=tr('dashboard.noRounds','Noch keine Runde');
if(subEl)subEl.innerText='';
}
return log.map(function(e){
return {ts:e.ts_s||0,time:e.timestamp||'',color:'#d4473f',text:'&#9763;&#65039; '+tr('dashboard.actInfection','Infection-Runde')+': '+resultLabel(e.result)};
});
}).catch(function(){return [];});
});
})();
</script>"""


def render_index_card_slot():
    """ui_slots-Ziel "index_card" - Karte auf der oeffentlichen Startseite
    index.html's <!--PLUGIN_SLOT:index_card--> Marker (gleiches Markup wie
    zuvor dort fest verdrahtet, jetzt nur noch bei aktivem Plugin - siehe
    render_dashboard_card_slot()-Docstring fuer die Begruendung des
    generischen Mechanismus)."""
    return (
        '<a class="card infection-card" href="/infection-view">'
        '<div class="cc-top"><h2><span class="dot" id="infection_dot"></span> <span data-i18n="index.infectionTitle">Infection Modus</span></h2>'
        '<span id="infection_state" class="infection-state" data-i18n="infection.inactive">Inaktiv</span><span class="cc-arrow">&#8594;</span></div>'
        '<p class="cc-text" id="infection_text" data-i18n="index.infectionText">Infizierten suchen, Rollen wechseln und Restzeit live verfolgen</p>'
        '</a>'
    )


def render_index_script_slot():
    """ui_slots-Ziel "index_script" - eigenstaendiges <script>-Fragment fuer
    index.html's <!--PLUGIN_SLOT:index_script--> Marker: registriert sich
    selbst in window.INDEX_HOOKS (vom Kern-Skript nach dem Laden von i18n
    einmalig aufgerufen, siehe index.html) und pollt/befuellt ab dann
    eigenstaendig render_index_card_slot()'s Karte - gleiches Muster wie
    render_gamemodes_script_slot() in den anderen Spielmodus-Plugins."""
    return """<script>
(function(){
window.INDEX_HOOKS=window.INDEX_HOOKS||[];
function tr(k,f){return (window.t||function(_k,fallback){return fallback;})(k,f);}
function update(d){
var dot=document.getElementById('infection_dot');
if(!dot)return;
var running=!!d.running,role=d.role||'seeker';
var state=document.getElementById('infection_state'),text=document.getElementById('infection_text');
dot.classList.toggle('on',running);
state.innerText=running?(role==='host'?tr('infection.host','Infizierter'):tr('infection.seeker','Sucher')):tr('infection.inactive','Inaktiv');
if(running){var sec=Math.max(0,Number(d.remaining_seconds)||0);text.innerText=tr('index.infectionRemaining','Restzeit')+': '+Math.floor(sec/60)+':'+String(sec%60).padStart(2,'0')+' | '+(d.last_event||'');}
else{text.innerHTML=tr('index.infectionText','Infizierten suchen, Rollen wechseln und Restzeit live verfolgen');}
}
function poll(){fetch('/infection-data',{cache:'no-store'}).then(function(r){return r.json();}).then(update).catch(function(){}).finally(function(){setTimeout(poll,1000);});}
window.INDEX_HOOKS.push(poll);
})();
</script>"""


# ==================== Plugin-Lifecycle (siehe plugin_manager.py) ====================

_manager = None
_task = None


def setup(context):
    """Erzeugt einmalig die InfectionMode-Instanz und startet ihren eigenen
    Dauerlauf-Task (BLE-Scan/Lobby/Runden-Timing) - ein Deaktivieren+
    Aktivieren erzeugt bewusst KEINE neue Instanz, nur einen neuen Task fuer
    dieselbe Instanz (siehe teardown())."""
    global _manager, _task
    if _manager is None:
        _manager = InfectionMode(AP_SSID, AP_PASSWORD, DEFAULT_PILOT_NAME, context["debug_log"])
    if _task is None:
        _task = asyncio.create_task(_manager.run())
    context["debug_log"]("[infection] Plugin aktiviert")


def teardown():
    """Stoppt den Dauerlauf-Task und eine laufende Runde - die
    InfectionMode-Instanz selbst bleibt bestehen (siehe setup())."""
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


def get_status():
    """Fuer main.py's generischen /data-Hook (siehe main.py's
    _infection_status_hook()) - liefert None, wenn das Plugin (noch) keine
    Instanz erzeugt hat."""
    return _manager.status() if _manager is not None else None


def get_session_summary_text():
    """Fuer main.py's Arcade-Sitzungs-Export (siehe main.py's
    build_session_txt_content())."""
    return _manager.session_summary_text() if _manager is not None else ""


async def handle_route(writer, request_path, request_method, query_params, body_params):
    if request_path == "/admin-infection":
        try:
            import pico_web_api
            await pico_web_api.send_admin_html_with_slot(writer, ADMIN_INFECTION_HTML_PATH, "dashboard_nav")
        except ImportError:
            if _send_html_file is not None:
                await _send_html_file(writer, ADMIN_INFECTION_HTML_PATH)
        return True

    if request_path == "/infection-view":
        if _send_html_file is not None:
            await _send_html_file(writer, INFECTION_VIEW_HTML_PATH)
        return True

    if (request_path.startswith("/infection-") or request_path.startswith("/lobby-")) and _manager is not None:
        return await _handle_infection_route(writer, request_path, request_method, query_params, body_params, _manager)

    return False
