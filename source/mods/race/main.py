"""race - Rennmodus als Plugin-Huelle um race_mode.py's BLE-Spiellogik.

Anders als shooter (komplette Spiellogik lebt im Mod-Ordner) bleibt die
eigentliche BLE-Spiellogik (RaceMode-Klasse, Advertise/Scan-Protokoll,
handle_race_route()) bewusst in source/race_mode.py, also fest Teil der
Firmware (siehe koth_mode.py fuer denselben Ansatz) - dieses Modul ist nur
eine duenne Plugin-Huelle darum: erzeugt/startet die eine RaceMode-Instanz
inkl. ihres eigenstaendigen asyncio-Tasks (RaceMode.run()) und stellt die
Web-/Dashboard-/Zuschauer-Ansicht-Integration (Routen, ui_slots, ui_pages)
bereit, die frueher fest in main.py/gmr.py verdrahtet war. Referenz fuer
dieses Muster (Firmware-Logik + duenne Plugin-Huelle) siehe template/README.md.

Lifecycle: setup() erstellt einmalig eine RaceMode-Instanz und startet
deren run()-Task (Dauerschleife mit BLE-Scan/Timing) - ein erneutes
Aktivieren nach dem Deaktivieren startet nur einen neuen Task fuer dieselbe
Instanz, siehe teardown(). Kein loop() noetig: RaceMode treibt sich ueber
ihren eigenen Task selbst, nicht ueber plugin_manager.run_loops()'s
Intervall-Tick (anders als shooter, das synchron per step() gepollt wird).
"""

import asyncio

from race_mode import RaceMode, handle_race_route as _handle_race_route

try:
    from main import DEFAULT_PILOT_NAME
except Exception:
    DEFAULT_PILOT_NAME = ""

try:
    from main import send_html_file as _send_html_file
except Exception:
    _send_html_file = None

ADMIN_RACE_HTML_PATH = "mods/race/admin_race.html"


def get_ui_schema():
    """Native UI-Beschreibung fuer die Android-App (siehe manifest.json's
    "ui_pages" und plugin_manager.get_ui_schema()) - inhaltliches Pendant zu
    admin_race.html, siehe source/mods/shooter/main.py's get_ui_schema()
    fuer die Erklaerung des Mechanismus."""
    return {
        "title": "Race",
        "poll_endpoint": "/race-data",
        "poll_interval_ms": 500,
        "sections": [
            {
                "type": "stats",
                "title": "Live-Status",
                "fields": [
                    {"key": "running", "label": "Zustand", "kind": "bool_text", "true_text": "Aktiv", "false_text": "Inaktiv"},
                    {"key": "last_event", "label": "Letztes Ereignis", "kind": "text"},
                    {"key": "waiting_for", "label": "Wartet auf Tor", "kind": "text"},
                    {"key": "lap_index", "label": "Runde", "kind": "text"},
                    {"key": "laps_total", "label": "Rundenzahl (Ziel)", "kind": "text"},
                    {"key": "gate_a_in_range", "label": "Tor A", "kind": "bool_text", "true_text": "In Reichweite", "false_text": "Ausser Reichweite"},
                    {"key": "gate_b_in_range", "label": "Tor B", "kind": "bool_text", "true_text": "In Reichweite", "false_text": "Ausser Reichweite"},
                    {"key": "node_id", "label": "Node ID", "kind": "text"},
                ],
            },
            {
                "type": "form",
                "title": "Renneinstellungen",
                "submit_endpoint": "/race-config",
                "submit_label": "Speichern & Starten",
                "hint": (
                    "Zwei Picos werden als Tor A und Tor B aufgestellt und senden nur "
                    "einen BLE-Beacon. Ein dritter Pico (Rennfahrer) misst die Zeit "
                    "zwischen dem Passieren von Tor A und Tor B, fuer die eingestellte "
                    "Rundenzahl. Die Tor-Rollen sind nur auf einem als Gate/Hill Pico "
                    "eingerichteten Geraet waehlbar."
                ),
                "fields": [
                    {"key": "enabled", "label": "Modus aktivieren", "kind": "toggle"},
                    {"key": "laps", "label": "Rundenzahl", "kind": "number", "min": 1, "max": 99},
                    {"key": "rssi_threshold", "label": "Naeheschwelle RSSI (dBm)", "kind": "number", "min": -95, "max": -20},
                    {"key": "cooldown_seconds", "label": "Sperrzeit (Sek.)", "kind": "number", "min": 1, "max": 30},
                ],
            },
            {
                "type": "actions",
                "buttons": [
                    {"label": "Rennen stoppen", "endpoint": "/race-stop", "style": "muted"},
                ],
            },
            {
                "type": "list",
                "title": "Rundenzeiten",
                "source_key": "lap_times_ms",
                "item_label_key": None,
                "item_label_prefix": "Runde",
                "item_value_key": None,
                "empty_text": "Noch keine Runde abgeschlossen.",
            },
        ],
    }


def render_dashboard_nav_slot():
    """ui_slots-Ziel "dashboard_nav" - siehe source/mods/shooter/main.py's
    render_dashboard_nav_slot()-Docstring fuer die Begruendung des generischen
    Mechanismus."""
    return '<a href="/admin-race" data-i18n="nav.race">Race</a>'


def render_dashboard_card_slot():
    """ui_slots-Ziel "dashboard_card" - Dashboard-Kachel fuer
    admin_dashboard.html's <!--PLUGIN_SLOT:dashboard_card--> Marker (gleiches
    Markup wie zuvor fest verdrahtet, jetzt nur noch bei aktivem Plugin;
    ".card.race" bleibt als CSS-Klasse im Kern-Template erhalten)."""
    return (
        '<a class="card race" href="/admin-race">'
        '<h3 data-i18n="dashboard.card.raceTitle">&#127937; Race Modus</h3>'
        '<p data-i18n="dashboard.card.raceText">Rundenzeit zwischen 2 Toren mit einstellbarer Rundenzahl messen</p>'
        '</a>'
    )


def render_dashboard_stat_slot():
    """ui_slots-Ziel "dashboard_stat" - Statistikkachel fuer
    admin_dashboard.html's <!--PLUGIN_SLOT:dashboard_stat--> Marker. Werte
    werden von render_dashboard_script_slot()'s eigenem DASHBOARD_HOOKS-
    Eintrag befuellt, NICHT mehr vom Dashboard-Kern-Skript."""
    return (
        '<div class="stile" style="--sc:#2980b9"><div class="sticon">&#127937;</div>'
        '<div class="stbody"><div class="stlabel" data-i18n="dashboard.statRaceLabel">Race</div>'
        '<div class="stval" id="st_race_val">-</div><div class="stsub" id="st_race_sub"></div></div></div>'
    )


def render_dashboard_script_slot():
    """ui_slots-Ziel "dashboard_script" - eigenstaendiges <script>-Fragment
    fuer admin_dashboard.html's <!--PLUGIN_SLOT:dashboard_script--> Marker,
    siehe source/mods/shooter/main.py's render_dashboard_script_slot()-
    Docstring fuer die Erklaerung des Musters (DASHBOARD_HOOKS)."""
    return """<script>
(function(){
window.DASHBOARD_HOOKS=window.DASHBOARD_HOOKS||[];
var COLOR='#2980b9';
function fmtMs(ms){
if(ms===null||ms===undefined)return '-';
var total=Math.round(ms),m=Math.floor(total/60000),s=Math.floor((total%60000)/1000),r=total%1000;
return (m>0?m+':'+String(s).padStart(2,'0'):String(s))+'.'+String(r).padStart(3,'0')+'s';
}
window.DASHBOARD_HOOKS.push(function(){
return fetch('/race-log',{cache:'no-store'}).then(function(r){return r.json();}).then(function(d){
var log=d.log||[];
var tr=window.t||function(k,f){return f;};
var valEl=document.getElementById('st_race_val'),subEl=document.getElementById('st_race_sub');
if(log.length){
var best=log.reduce(function(a,b){return (b.total_ms||Infinity)<(a.total_ms||Infinity)?b:a;},log[0]);
if(valEl)valEl.innerText=fmtMs(best.total_ms);
if(subEl)subEl.innerText=log.length+' '+tr('dashboard.rounds','Runden');
}else{
if(valEl)valEl.innerText=tr('dashboard.noRounds','Noch keine Runde');
if(subEl)subEl.innerText='';
}
return log.map(function(e){
return {ts:e.ts_s||0,time:e.timestamp||'',color:COLOR,text:'&#127937; '+tr('dashboard.actRace','Rennen beendet')+': '+fmtMs(e.total_ms)};
});
}).catch(function(){return [];});
});
})();
</script>"""


def render_gamemodes_button_slot():
    """ui_slots-Ziel "gamemodes_button" - Steuer-Button fuer die oeffentliche
    Zuschauer-Ansicht gamemodes_view.html's <!--PLUGIN_SLOT:gamemodes_button-->
    Marker (".b.race" bleibt als CSS-Klasse im Kern-Template erhalten)."""
    return '<a class="b race" href="/admin-race" data-i18n="gamemodesView.controlRaceButton">&#127937; Race steuern</a>'


def render_gamemodes_card_slot():
    """ui_slots-Ziel "gamemodes_card" - Live-Status-Karte fuer
    gamemodes_view.html's <!--PLUGIN_SLOT:gamemodes_card--> Marker (gleiches
    Markup wie zuvor dort fest verdrahtet, jetzt nur noch bei aktivem Plugin
    eingeblendet - siehe source/mods/shooter/main.py's
    render_gamemodes_card_slot()-Docstring)."""
    return (
        '<div class="game" style="--gc:#2980b9">'
        '<h2><span class="dot" id="r_dot"></span> <span data-i18n="gamemodesView.raceSection">&#127937; Race Modus</span></h2>'
        '<div class="grid">'
        '<div class="st"><span data-i18n="race.state">Zustand</span><b id="r_role">-</b></div>'
        '<div class="st"><span data-i18n="race.event">Letztes Ereignis</span><b id="r_event">-</b></div>'
        '<div class="st"><span data-i18n="race.waitingFor">Wartet auf</span><b id="r_waiting">-</b></div>'
        '<div class="st"><span data-i18n="race.lapProgress">Runde</span><b id="r_lapprogress">-</b></div>'
        '<div class="st"><span data-i18n="race.gateA">Tor A</span><b id="r_gatea">-</b></div>'
        '<div class="st"><span data-i18n="race.gateB">Tor B</span><b id="r_gateb">-</b></div>'
        '<div class="st"><span data-i18n="race.lastLap">Letzte Runde</span><b id="r_lastlap">-</b></div>'
        '<div class="st"><span data-i18n="race.bestLap">Beste Runde</span><b id="r_bestlap">-</b></div>'
        '<div class="st"><span data-i18n="race.totalTime">Gesamtzeit</span><b id="r_total">-</b></div>'
        '</div>'
        '<div class="hint" data-i18n="race.lapHistoryTitle">Rundenzeiten</div>'
        '<div id="r_laphistory"></div>'
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
function fmtMs(ms){if(ms===null||ms===undefined)return '-';return (Number(ms)/1000).toFixed(2)+'s';}
function showLapHistory(d){
var list=document.getElementById('r_laphistory'),laps=Array.isArray(d.lap_times_ms)?d.lap_times_ms:[];
list.replaceChildren();
if(!laps.length){var row=document.createElement('div');row.className='hint';row.innerText=tr('race.noLaps','Noch keine Runde abgeschlossen.');list.appendChild(row);return;}
laps.forEach(function(ms,i){var row=document.createElement('div');row.className='lap';var label=document.createElement('span');label.innerText=tr('race.lap','Runde')+' '+(i+1);var val=document.createElement('b');val.innerText=fmtMs(ms);row.append(label,val);list.appendChild(row);});
}
function update(d){
var running=!!d.running,role=running?d.role:'inactive';
document.getElementById('r_dot').classList.toggle('on',running);
var roleLabels={racer:tr('race.roleRacer','Rennfahrer'),gate_a:tr('race.roleGateA','Tor A'),gate_b:tr('race.roleGateB','Tor B'),inactive:tr('race.inactive','Inaktiv')};
document.getElementById('r_role').innerText=roleLabels[role]||role;
document.getElementById('r_event').innerText=d.last_event||'-';
document.getElementById('r_waiting').innerText=d.waiting_for?('Tor '+d.waiting_for):'-';
document.getElementById('r_lapprogress').innerText=d.finished?tr('race.finished','Fertig'):(d.lap_index+1)+' / '+d.laps_total;
document.getElementById('r_gatea').innerText=d.gate_a_in_range?tr('race.inRange','In Reichweite'):tr('race.outOfRange','Ausser Reichweite');
document.getElementById('r_gateb').innerText=d.gate_b_in_range?tr('race.inRange','In Reichweite'):tr('race.outOfRange','Ausser Reichweite');
document.getElementById('r_lastlap').innerText=fmtMs(d.last_lap_ms);
document.getElementById('r_bestlap').innerText=fmtMs(d.best_lap_ms);
document.getElementById('r_total').innerText=fmtMs(d.total_ms);
showLapHistory(d);
}
function poll(){fetch('/race-data',{cache:'no-store'}).then(function(r){return r.json();}).then(update).catch(function(){}).finally(function(){setTimeout(poll,500);});}
window.GAMEMODES_HOOKS.push(poll);
})();
</script>"""


# ==================== Plugin-Lifecycle (siehe plugin_manager.py) ====================

_manager = None
_task = None


def setup(context):
    """Erzeugt einmalig die RaceMode-Instanz und startet ihren eigenen
    Dauerlauf-Task (BLE-Scan/Renn-Timing) - ein Deaktivieren+Aktivieren
    erzeugt bewusst KEINE neue Instanz, nur einen neuen Task fuer dieselbe
    Instanz (siehe teardown())."""
    global _manager, _task
    if _manager is None:
        _manager = RaceMode(DEFAULT_PILOT_NAME, context["debug_log"])
    if _task is None:
        _task = asyncio.create_task(_manager.run())
    context["debug_log"]("[race] Plugin aktiviert (node_id=%s)" % _manager.node_id)


def teardown():
    """Stoppt den Dauerlauf-Task und ein laufendes Rennen - die RaceMode-
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
            _manager.stop_race("Plugin deaktiviert")
        except Exception:
            pass


async def handle_route(writer, request_path, request_method, query_params, body_params):
    if request_path == "/admin-race":
        try:
            import pico_web_api
            await pico_web_api.send_admin_html_with_slot(writer, ADMIN_RACE_HTML_PATH, "dashboard_nav")
        except ImportError:
            if _send_html_file is not None:
                await _send_html_file(writer, ADMIN_RACE_HTML_PATH)
        return True

    if request_path.startswith("/race-") and _manager is not None:
        return await _handle_race_route(writer, request_path, request_method, query_params, body_params, _manager)

    return False
