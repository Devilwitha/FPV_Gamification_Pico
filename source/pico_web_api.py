"""pico_web_api.py - HTTP-Endpunkte fuer das Plugin-/Store-System.

Buendelt (gleiches Auslagerungs-Muster wie gmr.py/idcard_helpers.py, siehe
dortige Docstrings): die "/admin-plugins"-Seite (zwei Unter-Tabs:
Installierte Plugins / Webshop Store & Updates), die zugehoerigen
"/api/plugins*"-, "/api/store*"- und "/api/firmware*"-JSON-Endpunkte, sowie
send_admin_html_with_slot() fuer die Plugin-Tab-Erweiterung (siehe
admin_system.html/admin_idcard.html's <!--PLUGIN_SLOT:...--> Marker).

Die "/admin-plugins"-Seite existiert bewusst NUR als Python-String (kein
zusaetzliches .html-File) - anders als die uebrigen Admin-Seiten, die alle
byte-genau von der Platte gestreamt werden (siehe main.py's send_html_file()
Docstring). main.py/gmr.py bleiben dadurch unangetastet klein.

Lazy importiert aus main.py, erst beim ersten "/admin-plugins" bzw.
"/api/plugins*"-Request.
"""

import gc
import json

from main import send_html_file, debug_log


async def _send_json(writer, payload, status="200 OK"):
    body = json.dumps(payload).encode()
    writer.write(("HTTP/1.1 " + status + "\r\n").encode())
    writer.write(b"Content-Type: application/json\r\n")
    writer.write(b"Cache-Control: no-store\r\n")
    writer.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(body)
    await writer.drain()


async def send_admin_html_with_slot(writer, file_path, slot_name):
    """Wie main.send_html_file(), ersetzt aber zusaetzlich den
    <!--PLUGIN_SLOT:slot_name--> Marker durch von Plugins bereitgestelltes
    HTML (siehe plugin_manager.get_ui_slot_html()). Faellt auf das
    unveraenderte, chunked-gestreamte send_html_file() zurueck, wenn kein
    Plugin diesen Slot nutzt - Null-Overhead im Normalfall."""
    import plugin_manager

    slot_html = plugin_manager.get_ui_slot_html(slot_name)
    if not slot_html:
        await send_html_file(writer, file_path)
        return

    marker = "<!--PLUGIN_SLOT:{}-->".format(slot_name)
    with open(file_path, "r") as f:
        content = f.read()
    content = content.replace(marker, slot_html)
    data = content.encode("utf-8")

    writer.write(b"HTTP/1.1 200 OK\r\n")
    writer.write(b"Content-Type: text/html; charset=utf-8\r\n")
    writer.write(b"Content-Length: " + str(len(data)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(data)
    await writer.drain()
    gc.collect()


ADMIN_PLUGINS_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin - Plugins</title>
<style>
body{background:#0a0c12;color:#e8f0f8;margin:0;padding:12px;font-family:monospace}
h1{color:#1abc9c;font-size:1.3em;margin:0 0 10px}
.c{max-width:700px;margin:0 auto;background:#141b25;padding:20px;border:2px solid #1abc9c;border-radius:8px}
.nv{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px}
.nv a{flex:1 1 80px;text-align:center;padding:8px 4px;background:#1a2637;color:#9fb4cb;border-radius:6px;text-decoration:none;font-size:.78em;border:1px solid #2a3b52}
.nv a.on{background:#1abc9c;color:#04231c;border-color:#1abc9c;font-weight:bold}
.tabs{display:flex;gap:6px;margin-bottom:14px}
.tabbtn{flex:1;padding:9px;background:#1a2637;color:#9fb4cb;border:1px solid #335174;border-radius:6px;cursor:pointer;font-family:monospace;font-size:.85em}
.tabbtn.on{background:#1abc9c;color:#04231c;font-weight:bold;border-color:#1abc9c}
.panel{display:none}
.panel.on{display:block}
.s{background:#1a2637;padding:15px;border:1px solid #335174;border-radius:8px;margin:10px 0}
.plugin{display:flex;justify-content:space-between;align-items:center;padding:9px 0;border-bottom:1px solid rgba(255,255,255,.07);gap:10px}
.plugin:last-child{border-bottom:0}
.pname{font-weight:bold}
.pver{color:#9fb4cb;font-size:.78em;margin-left:6px}
.perr{color:#e74c3c;font-size:.8em;margin-top:3px}
.crashed{color:#e74c3c;font-weight:bold}
.btns{display:flex;gap:6px;flex-shrink:0}
.b{background:#53657a;color:#fff;border:0;border-radius:4px;padding:7px 10px;cursor:pointer;font-family:monospace;font-size:.78em}
.b.del{background:#c0392b}
.b.dl{background:#1abc9c;color:#04231c;font-weight:bold}
.toggle{position:relative;display:inline-block;width:40px;height:22px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:#335174;transition:.2s;border-radius:22px}
.slider:before{position:absolute;content:"";height:16px;width:16px;left:3px;bottom:3px;background:#e8f0f8;transition:.2s;border-radius:50%}
input:checked+.slider{background:#1abc9c}
input:checked+.slider:before{transform:translateX(18px)}
.empty{color:#6f8299;font-size:.85em}
.msg{color:#9fb4cb;font-size:.82em;min-height:1.3em;margin-top:7px}
</style>
</head>
<body>
<div class="c">
<h1>&#129513; PLUGINS</h1>
<div class="nv">
<a href="/admin">Dashboard</a>
<a href="/admin-plugins" class="on">Plugins</a>
<a href="/admin-system">System</a>
<a href="/">Home</a>
</div>

<div class="tabs">
<button class="tabbtn on" id="tab_installed" onclick="showTab('installed')">Installierte Plugins</button>
<button class="tabbtn" id="tab_store" onclick="showTab('store')">Webshop Store &amp; Updates</button>
</div>

<div class="panel on" id="panel_installed">
<div class="s">
<h2>Installierte Plugins</h2>
<div id="pluginList" class="empty">Lade...</div>
<div id="pluginMsg" class="msg"></div>
</div>
</div>

<div class="panel" id="panel_store">
<div class="s">
<h2>Firmware</h2>
<div id="fwStatus" class="empty">Lade...</div>
</div>
<div class="s">
<h2>Webshop-Mods</h2>
<div id="storeList" class="empty">Lade...</div>
<div id="storeMsg" class="msg"></div>
</div>
</div>

</div>
<script>
function showTab(name){
  document.getElementById('tab_installed').classList.toggle('on', name==='installed');
  document.getElementById('tab_store').classList.toggle('on', name==='store');
  document.getElementById('panel_installed').classList.toggle('on', name==='installed');
  document.getElementById('panel_store').classList.toggle('on', name==='store');
}

function loadPlugins(){
  fetch('/api/plugins',{cache:'no-store'}).then(r=>r.json()).then(list=>{
    const el=document.getElementById('pluginList');
    if(!list.length){el.innerHTML='<div class="empty">Keine Plugins installiert</div>';return;}
    el.innerHTML='';
    list.forEach(p=>{
      const row=document.createElement('div');
      row.className='plugin';
      const info=document.createElement('div');
      info.innerHTML='<span class="pname'+(p.has_error?' crashed':'')+'">'+p.name+'</span><span class="pver">v'+p.version+(p.author?' &middot; Code by '+p.author:'')+'</span>'+
        (p.has_error?'<div class="perr">CRASHED / FEHLER: '+p.error_message+'</div>':'');
      const btns=document.createElement('div');
      btns.className='btns';
      const label=document.createElement('label');
      label.className='toggle';
      label.innerHTML='<input type="checkbox" '+(p.enabled?'checked':'')+' onchange="togglePlugin(\\''+p.name+'\\',this.checked)"><span class="slider"></span>';
      const del=document.createElement('button');
      del.className='b del';
      del.innerText='Loeschen';
      del.onclick=()=>deletePlugin(p.name);
      btns.appendChild(label);
      btns.appendChild(del);
      row.appendChild(info);
      row.appendChild(btns);
      el.appendChild(row);
    });
  }).catch(()=>{document.getElementById('pluginList').innerHTML='<div class="empty">Fehler beim Laden</div>';});
}

function togglePlugin(name,enabled){
  fetch('/api/plugins/'+encodeURIComponent(name)+'/toggle',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'enabled='+(enabled?'1':'0')})
    .then(()=>loadPlugins());
}

function deletePlugin(name){
  if(!confirm('Plugin "'+name+'" wirklich loeschen?'))return;
  fetch('/api/plugins/'+encodeURIComponent(name)+'/delete',{method:'POST'}).then(()=>loadPlugins());
}

function loadFirmwareStatus(){
  fetch('/api/firmware/status',{cache:'no-store'}).then(r=>r.json()).then(s=>{
    const el=document.getElementById('fwStatus');
    el.innerHTML='Aktuelle Version: <b>'+(s.fw_current_version||'-')+'</b><br>'+
      (s.fw_update_available?'<b style="color:#1abc9c">Update verfuegbar: '+s.fw_latest_version+'</b> (siehe Update-Seite)':'Kein Update verfuegbar');
  }).catch(()=>{document.getElementById('fwStatus').innerHTML='Status nicht verfuegbar';});
}

function loadStoreList(){
  fetch('/api/store/list',{cache:'no-store'}).then(r=>r.json()).then(data=>{
    const el=document.getElementById('storeList');
    const plugins=data.plugins||[];
    if(!plugins.length){el.innerHTML='<div class="empty">Keine Mods synchronisiert (WLAN pruefen)</div>';return;}
    el.innerHTML='';
    plugins.forEach(p=>{
      const row=document.createElement('div');
      row.className='plugin';
      row.innerHTML='<div><span class="pname">'+p.name+'</span><span class="pver">v'+p.version+(p.author?' &middot; Code by '+p.author:'')+'</span></div>';
      const btn=document.createElement('button');
      btn.className='b dl';
      btn.innerText='Download';
      btn.onclick=()=>downloadPlugin(p.name);
      row.appendChild(btn);
      el.appendChild(row);
    });
  }).catch(()=>{document.getElementById('storeList').innerHTML='<div class="empty">Keine Daten</div>';});
}

function downloadPlugin(name){
  document.getElementById('storeMsg').innerText='Starte WLAN-Download von "'+name+'"...';
  fetch('/api/store/download',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'name='+encodeURIComponent(name)})
    .then(r=>r.json()).then(res=>{
      document.getElementById('storeMsg').innerText=res.ok?'Download gestartet - Pico verbindet sich kurz mit dem WLAN.':('Fehler: '+res.error);
    }).catch(()=>{document.getElementById('storeMsg').innerText='Fehler beim Start des Downloads';});
}

loadPlugins();
loadFirmwareStatus();
loadStoreList();
</script>
</body>
</html>
"""


async def _send_html_string(writer, html):
    data = html.encode("utf-8")
    writer.write(b"HTTP/1.1 200 OK\r\n")
    writer.write(b"Content-Type: text/html; charset=utf-8\r\n")
    writer.write(b"Content-Length: " + str(len(data)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(data)
    await writer.drain()


def _build_network_deps():
    import boot_runtime
    from hotspot_common import configure_hotspot, load_wlan_config
    import main as _main

    return {
        "log": debug_log,
        "feed_wdt": boot_runtime.feed_wdt,
        "load_wlan_config": load_wlan_config,
        "configure_hotspot": configure_hotspot,
        "ap_ssid": _main.AP_SSID,
        "ap_password": _main.AP_PASSWORD,
        "firmware_version": _main.FIRMWARE_VERSION,
        "repo_owner": _main.GITHUB_REPO_OWNER,
        "repo_name": _main.GITHUB_REPO_NAME,
        "asset_name": _main.GITHUB_OTA_ASSET_NAME,
    }


async def _run_store_download(plugin_name):
    """Wird per asyncio.create_task() gestartet, NACHDEM die HTTP-Antwort
    des Auslsers bereits verschickt wurde - gleiches bewusst blockierende
    Muster wie main.py's _run_github_ota_update() (siehe dortiger
    Docstring): waehrend des WLAN-Downloads ist der eigene Access Point
    ohnehin kurz weg, es gibt niemanden sonst zu bedienen."""
    import network_manager
    try:
        network_manager.download_plugin_via_wifi(plugin_name, _build_network_deps())
    except Exception as e:
        debug_log("[STORE] Unerwarteter Fehler beim Download von '{}': {}".format(plugin_name, e))


async def handle_pico_api_route(writer, request_path, request_method, query_params, body_params):
    import plugin_manager
    import network_manager
    import asyncio

    if request_path == "/admin-plugins":
        await _send_html_string(writer, ADMIN_PLUGINS_HTML)
        return True

    if request_path == "/api/plugins":
        await _send_json(writer, plugin_manager.list_plugins())
        return True

    if request_path.startswith("/api/plugins/") and request_path.endswith("/toggle") and request_method == "POST":
        name = request_path[len("/api/plugins/"):-len("/toggle")]
        enabled = body_params.get("enabled", "0") in ("1", "true", "on")
        manifest = plugin_manager.set_plugin_state(name, enabled)
        await _send_json(writer, {"ok": True, "plugin": manifest})
        return True

    if request_path.startswith("/api/plugins/") and request_path.endswith("/delete") and request_method == "POST":
        name = request_path[len("/api/plugins/"):-len("/delete")]
        plugin_manager.delete_plugin(name)
        await _send_json(writer, {"ok": True})
        return True

    if request_path == "/api/firmware/status":
        await _send_json(writer, network_manager.network_state)
        return True

    if request_path == "/api/store/list":
        await _send_json(writer, network_manager.load_store_cache())
        return True

    if request_path == "/api/store/download" and request_method == "POST":
        name = str(body_params.get("name", "")).strip()
        if not name:
            await _send_json(writer, {"ok": False, "error": "Kein Mod-Name angegeben"}, "400 Bad Request")
            return True
        asyncio.create_task(_run_store_download(name))
        await _send_json(writer, {"ok": True})
        return True

    return False
