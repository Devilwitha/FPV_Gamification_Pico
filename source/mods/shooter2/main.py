"""shooter2 - TEMPORAERES Test-Plugin, nur um zu zeigen, dass ein neues
Plugin sich automatisch (ohne Aenderung an admin_dashboard.html/main.py) in
Dashboard-Nav/-Karte/-Statistik und Webshop-Store einklinkt, sobald es unter
mods/ liegt und aktiviert ist (siehe manifest.json's "ui_slots" +
plugin_manager.get_ui_slot_html()). Bewusst KEINE echte IR-Hardware wie
source/mods/shooter/main.py (kein GPIO-Konflikt bei gleichzeitig aktivem
"shooter"-Plugin) - nur ein Zaehler + Fake-Log. Wird nach dem Test wieder
komplett geloescht.
"""

import json
import time

_counter = {"ticks": 0}


def setup(context):
    context["debug_log"]("[shooter2] Test-Plugin aktiviert")


def loop():
    _counter["ticks"] += 1


def teardown():
    pass


async def _send_json(writer, payload):
    body = json.dumps(payload).encode()
    writer.write(b"HTTP/1.1 200 OK\r\n")
    writer.write(b"Content-Type: application/json\r\n")
    writer.write(b"Cache-Control: no-store\r\n")
    writer.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(body)


async def handle_route(writer, request_path, request_method, query_params, body_params):
    if request_path == "/admin-shooter2":
        body = (
            "<body style='background:#0a0c12;color:#e8f0f8;font-family:monospace;padding:20px'>"
            "<h1>Shooter2 (Test)</h1><p>Ticks seit Aktivierung: {}</p>"
            "<p><a style='color:#9b59b6' href='/admin'>&larr; zurueck zum Dashboard</a></p></body>"
        ).format(_counter["ticks"]).encode()
        writer.write(b"HTTP/1.1 200 OK\r\n")
        writer.write(b"Content-Type: text/html; charset=utf-8\r\n")
        writer.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n")
        writer.write(b"Connection: close\r\n\r\n")
        writer.write(body)
        return True

    if request_path == "/shooter2-log":
        await _send_json(writer, {
            "ok": True,
            "log": [{
                "ts_s": int(time.time()),
                "timestamp": "Test",
                "hits_taken": _counter["ticks"],
                "shots_fired": _counter["ticks"],
            }],
        })
        return True

    if request_path == "/shooter2-data":
        # Datenquelle fuer render_gamemodes_script_slot()'s Poll - eigener,
        # winziger Endpunkt statt /shooter2-log wiederzuverwenden, weil die
        # Zuschauer-Ansicht (anders als das Dashboard) laufend pollt statt
        # nur beim Laden einmal abzufragen.
        await _send_json(writer, {"ticks": _counter["ticks"]})
        return True

    return False


def render_dashboard_nav_slot():
    return '<a href="/admin-shooter2">Shooter2 (Test)</a>'


def render_dashboard_card_slot():
    return (
        '<a class="card" style="border-left-color:#9b59b6" href="/admin-shooter2">'
        '<h3>&#129514; Shooter2 (Test)</h3>'
        '<p>Test-Plugin zur Kontrolle der dynamischen Dashboard-Anzeige</p>'
        '</a>'
    )


def render_dashboard_stat_slot():
    return (
        '<div class="stile" style="--sc:#9b59b6"><div class="sticon">&#129514;</div>'
        '<div class="stbody"><div class="stlabel">Shooter2 (Test)</div>'
        '<div class="stval" id="st_shooter2_val">-</div><div class="stsub" id="st_shooter2_sub"></div></div></div>'
    )


def render_gamemodes_button_slot():
    return '<a class="b" style="border-left:3px solid #9b59b6" href="/admin-shooter2">&#129514; Shooter2 (Test) steuern</a>'


def render_gamemodes_card_slot():
    return (
        '<div class="game" style="--gc:#9b59b6">'
        '<h2><span class="dot on" id="s2_dot"></span> &#129514; Shooter2 (Test)</h2>'
        '<div class="grid">'
        '<div class="st"><span>Ticks seit Aktivierung</span><b id="s2_ticks">-</b></div>'
        '</div>'
        '<div class="hint">Reines Test-Plugin zur Kontrolle der generischen Zuschauer-Karte.</div>'
        '</div>'
    )


def render_gamemodes_script_slot():
    return """<script>
(function(){
window.GAMEMODES_HOOKS=window.GAMEMODES_HOOKS||[];
function poll(){fetch('/shooter2-data',{cache:'no-store'}).then(function(r){return r.json();}).then(function(d){
var el=document.getElementById('s2_ticks');
if(el)el.innerText=d.ticks||0;
}).catch(function(){}).finally(function(){setTimeout(poll,1000);});}
window.GAMEMODES_HOOKS.push(poll);
})();
</script>"""


def render_dashboard_script_slot():
    return """<script>
(function(){
window.DASHBOARD_HOOKS=window.DASHBOARD_HOOKS||[];
var COLOR='#9b59b6';
window.DASHBOARD_HOOKS.push(function(){
return fetch('/shooter2-log',{cache:'no-store'}).then(function(r){return r.json();}).then(function(d){
var log=d.log||[];
var valEl=document.getElementById('st_shooter2_val'),subEl=document.getElementById('st_shooter2_sub');
if(log.length){
if(valEl)valEl.innerText=log[0].hits_taken+' Ticks';
if(subEl)subEl.innerText='Test-Plugin aktiv';
}
return log.map(function(e){return {ts:e.ts_s||0,time:e.timestamp||'',color:COLOR,text:'&#129514; Shooter2-Test: '+e.hits_taken+' Ticks'};});
}).catch(function(){return [];});
});
})();
</script>"""
