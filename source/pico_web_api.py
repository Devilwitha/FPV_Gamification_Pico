"""pico_web_api.py - HTTP-Endpunkte fuer das Plugin-/Store-System.

Buendelt (gleiches Auslagerungs-Muster wie gmr.py/idcard_helpers.py, siehe
dortige Docstrings): die "/admin-plugins"-Seite (zwei Unter-Tabs:
Installierte Plugins / Webshop Store & Updates), die zugehoerigen
"/api/plugins*"-, "/api/store*"-, "/api/firmware*"- und "/api/plugin-ui/*"-
JSON-Endpunkte, sowie send_admin_html_with_slot() fuer die Plugin-Tab-
Erweiterung (siehe admin_system.html/admin_idcard.html/admin_dashboard.html's
<!--PLUGIN_SLOT:...--> Marker).

"/api/plugin-ui/<name>" liefert das von plugin_manager.get_ui_schema()
gebaute JSON-Schema fuer die native Android-App (siehe android_app/.../
ui/plugins/PluginUiScreen.kt) - komplett getrennt von den PLUGIN_SLOT-HTML-
Fragmenten oben, die weiterhin ausschliesslich fuer die Browser-Oberflaeche
gedacht sind.

Die "/admin-plugins"-Seite existiert bewusst NUR als Python-String (kein
zusaetzliches .html-File) - anders als die uebrigen Admin-Seiten, die alle
byte-genau von der Platte gestreamt werden (siehe main.py's send_html_file()
Docstring). main.py/gmr.py bleiben dadurch unangetastet klein.

Lazy importiert aus main.py, erst beim ersten "/admin-plugins" bzw.
"/api/plugins*"-Request.
"""

import gc
import json
import os

from main import send_html_file, debug_log, safe_base64_file_to_file

# Staging-Dateien fuer den Plugin-ZIP-Upload (siehe _handle_plugin_upload_chunk()/
# _handle_plugin_upload_finalize()) - eigene Dateien statt der OTA-eigenen
# 'update.pbp' (upload_helpers.py), damit ein Plugin-Upload niemals mit einem
# parallel laufenden Firmware-Update kollidiert.
PLUGIN_UPLOAD_STAGING_B64 = "plugin_upload.zip.b64"
PLUGIN_UPLOAD_STAGING_BIN = "plugin_upload.zip"

# name -> "" ausserhalb eines laufenden Uploads; wird beim ersten Chunk
# (index=0) gesetzt und nach finalize()/Fehler wieder zurueckgesetzt.
_plugin_upload_state = {"total_chunks": 0, "received_chunks": 0, "name": ""}


def _sanitize_plugin_name(name):
    """Wie challenge_helpers.py's _sanitize_mission_name(): reiner ASCII-
    Bereichsvergleich statt str.isalnum() (auf diesem MicroPython-Build
    nicht verfuegbar, siehe dortiger Kommentar). Bestimmt gleichzeitig den
    Zielordnernamen mods/<name>/ - "shooter.zip" hochladen ergibt also
    mods/shooter/."""
    name = str(name or "").strip()
    cleaned = "".join(
        ch for ch in name
        if ("a" <= ch <= "z") or ("A" <= ch <= "Z") or ("0" <= ch <= "9") or ch in ("-", "_")
    )
    return cleaned[:40]


async def _send_json(writer, payload, status="200 OK"):
    body = json.dumps(payload).encode()
    writer.write(("HTTP/1.1 " + status + "\r\n").encode())
    writer.write(b"Content-Type: application/json\r\n")
    writer.write(b"Cache-Control: no-store\r\n")
    writer.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(body)
    await writer.drain()


async def send_admin_html_with_slot(writer, file_path, slot_names, static_slots=None):
    """Wie main.send_html_file(), ersetzt aber zusaetzlich <!--PLUGIN_SLOT:x-->
    Marker durch von Plugins bereitgestelltes HTML (siehe
    plugin_manager.get_ui_slot_html()). slot_names ist entweder ein einzelner
    Slot-Name (String, z.B. "system"/"idcard") oder eine Liste mehrerer
    Slot-Namen auf derselben Seite (z.B. admin_dashboard.html's
    "dashboard_nav"/"dashboard_card"/"dashboard_stat"/"dashboard_script" -
    so kann JEDES Plugin, das einen dieser Slots belegt, sich selbst in Nav/
    Karten/Statistik einklinken, OHNE dass admin_dashboard.html es kennen
    muss). static_slots (optional dict slot_name->html) ergaenzt/ueberschreibt
    die Plugin-gelieferten Fragmente um vom Aufrufer selbst berechnetes HTML
    (siehe send_index_html()'s "index_gamemodes_hub" - EINE zentral
    berechnete Sammel-Karte statt einer pro Plugin). Faellt auf das
    unveraenderte, chunked-gestreamte send_html_file() zurueck, wenn weder
    ein Plugin noch static_slots einen der Slots befuellt - Null-Overhead im
    Normalfall."""
    import plugin_manager

    if isinstance(slot_names, str):
        slot_names = [slot_names]

    slot_html_by_name = {}
    for slot_name in slot_names:
        html = plugin_manager.get_ui_slot_html(slot_name)
        if html:
            slot_html_by_name[slot_name] = html
    if static_slots:
        for slot_name, html in static_slots.items():
            if html:
                slot_html_by_name[slot_name] = html

    if not slot_html_by_name:
        await send_html_file(writer, file_path)
        return

    with open(file_path, "r") as f:
        content = f.read()
    for slot_name, html in slot_html_by_name.items():
        content = content.replace("<!--PLUGIN_SLOT:{}-->".format(slot_name), html)
    data = content.encode("utf-8")

    writer.write(b"HTTP/1.1 200 OK\r\n")
    writer.write(b"Content-Type: text/html; charset=utf-8\r\n")
    writer.write(b"Content-Length: " + str(len(data)).encode() + b"\r\n")
    writer.write(b"Connection: close\r\n\r\n")
    writer.write(data)


def _render_gamemodes_hub_card():
    """Sammel-Karte fuer index.html's <!--PLUGIN_SLOT:index_gamemodes_hub-->
    Marker: anders als normale ui_slots wird sie NICHT von jedem einzelnen
    Spielmodus-Plugin selbst geliefert (das gaebe eine Karte pro Plugin,
    z.B. getrennt fuer KOTH/Race/Shooter) - stattdessen genau EINMAL zentral
    gerendert, sobald IRGENDEIN aktives Plugin den bereits bestehenden
    "gamemodes_button"-Slot belegt (siehe gamemodes_view.html) - dadurch
    bleibt sie trotzdem generisch: sie kennt keinen einzelnen Plugin-Namen,
    sondern fragt nur, ob die gemeinsame Zuschauer-Seite /gamemodes-view
    ueberhaupt Inhalt haette."""
    import plugin_manager

    if not plugin_manager.get_ui_slot_html("gamemodes_button"):
        return ""
    return (
        '<a class="card gamemodes-card" href="/gamemodes-view">'
        '<div class="cc-top"><h2><span class="dot on"></span> <span data-i18n="index.gamemodesTitle">&#127942; Game Mods</span></h2><span class="cc-arrow">&#8594;</span></div>'
        '<p class="cc-text" data-i18n="index.gamemodesText">Aktuelle Spielmodi live verfolgen</p>'
        '</a>'
    )


async def send_index_html(writer, file_path):
    """Rendert index.html: die generischen "index_card"/"index_script"
    ui_slots (z.B. Infection's eigene Karte, siehe send_admin_html_with_slot())
    PLUS die zentral berechnete "index_gamemodes_hub"-Sammel-Karte (siehe
    _render_gamemodes_hub_card()). Von main.py's Routen-Fallback fuer "/"
    aufgerufen."""
    await send_admin_html_with_slot(
        writer, file_path, ["index_card", "index_script"],
        static_slots={"index_gamemodes_hub": _render_gamemodes_hub_card()},
    )
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
.pdesc{color:#9fb4cb;font-size:.8em;margin-top:3px}
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
input[type=file]{display:block;margin:10px 0;padding:8px;background:#0b1320;color:#e8f0f8;border:1px solid #335174;border-radius:4px;width:100%;box-sizing:border-box}
.pbwrap{display:none;margin:10px 0;height:20px;background:#0b1320;border:1px solid #335174;border-radius:4px;overflow:hidden;position:relative}
.pbbar{height:100%;width:0%;background:#1abc9c;transition:width .15s ease}
.pbtxt{position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;font-size:.72em;font-weight:bold;color:#fff;text-shadow:0 1px 2px #000}
</style>
</head>
<body>
<div class="c">
<h1 data-i18n="plugins.title">&#129513; PLUGINS</h1>
<div class="nv">
<a href="/admin" data-i18n="nav.dashboard">Dashboard</a>
<a href="/admin-update" data-i18n="nav.update">Update</a>
<a href="/admin-simulate" data-i18n="nav.simulation">Simulation</a>
<a href="/admin-profiles" data-i18n="nav.profiles">Profile</a>
<a href="/admin-system" data-i18n="nav.system">System</a>
<a href="/admin-idcard" data-i18n="nav.idcard">Ausweis</a>
<a href="/admin-challenges" data-i18n="nav.challenges">Challenges</a>
<!--PLUGIN_SLOT:dashboard_nav-->
<a href="/admin-plugins" class="on" data-i18n="nav.plugins">Plugins</a>
<a href="/admin-credits" data-i18n="nav.credits">Credits</a>
<a href="/" data-i18n="nav.home">Home</a>
</div>

<div class="tabs">
<button class="tabbtn on" id="tab_installed" onclick="showTab('installed')">Installierte Plugins</button>
<button class="tabbtn" id="tab_store" onclick="showTab('store')">Webshop Store &amp; Updates</button>
</div>

<div class="panel on" id="panel_installed">
<div class="s">
<h2>Plugin hochladen</h2>
<div style="color:#9fb4cb;font-size:.8em;margin-bottom:6px">ZIP-Datei eines Mod-Ordners hochladen (z.B. "shooter.zip" -&gt; mods/shooter/). Ein bestehendes Plugin mit gleichem Namen wird ersetzt.</div>
<input type="file" id="pf" accept=".zip">
<div id="pfi" style="color:#9fb4cb;font-size:0.9em">ZIP-Datei waehlen...</div>
<div id="pbwrap" class="pbwrap"><div id="pbbar" class="pbbar"></div><div id="pbtxt" class="pbtxt"></div></div>
<div id="ps" class="msg"></div>
<button class="b" onclick="uploadPluginZip()">Hochladen</button>
</div>
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

function esc(s){return (s==null?'':String(s)).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}

function loadPlugins(){
  fetch('/api/plugins',{cache:'no-store'}).then(r=>r.json()).then(list=>{
    const el=document.getElementById('pluginList');
    if(!list.length){el.innerHTML='<div class="empty">Keine Plugins installiert</div>';return;}
    el.innerHTML='';
    list.forEach(p=>{
      const row=document.createElement('div');
      row.className='plugin';
      const info=document.createElement('div');
      info.innerHTML='<span class="pname'+(p.has_error?' crashed':'')+'">'+esc(p.name)+'</span><span class="pver">v'+esc(p.version)+(p.author?' &middot; Code by '+esc(p.author):'')+'</span>'+
        (p.description?'<div class="pdesc">'+esc(p.description)+'</div>':'')+
        (p.has_error?'<div class="perr">CRASHED / FEHLER: '+esc(p.error_message)+'</div>':'');
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
      row.innerHTML='<div><span class="pname">'+esc(p.name)+'</span><span class="pver">v'+esc(p.version)+(p.author?' &middot; Code by '+esc(p.author):'')+'</span>'+
        (p.description?'<div class="pdesc">'+esc(p.description)+'</div>':'')+'</div>';
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

function pluginNameFromZipFilename(filename){
  return String(filename||'').replace(/\\.zip$/i,'').replace(/[^A-Za-z0-9_-]/g,'_').substring(0,40);
}

document.getElementById('pf').addEventListener('change',function(e){
  const f=e.target.files[0];
  document.getElementById('pfi').innerText=f?('Datei: '+f.name+' ('+(f.size/1024).toFixed(1)+' KB)'):'ZIP-Datei waehlen...';
  document.getElementById('pbwrap').style.display='none';
  document.getElementById('pbbar').style.width='0%';
  document.getElementById('pbtxt').innerText='';
  document.getElementById('ps').innerText='';
});

function uploadPluginZip(){
  const f=document.getElementById('pf').files[0],s=document.getElementById('ps');
  if(!f){s.innerText='Keine Datei!';return;}
  if(!f.name.toLowerCase().endsWith('.zip')){s.innerText='Nur .zip-Dateien erlaubt!';return;}
  const name=pluginNameFromZipFilename(f.name);
  if(!name){s.innerText='Ungueltiger Dateiname - bitte umbenennen (nur Buchstaben/Zahlen/_/-).';return;}
  const rd=new FileReader();
  rd.onload=function(e){
    try{
      const b=new Uint8Array(e.target.result);
      let bn='';
      for(let i=0;i<b.length;i+=10240){const ch=b.slice(i,i+10240);for(let j=0;j<ch.length;j++)bn+=String.fromCharCode(ch[j]);}
      const b64=btoa(bn),tc=Math.max(1,Math.ceil(b64.length/1024));
      let idx=0;
      const pbwrap=document.getElementById('pbwrap'),pbbar=document.getElementById('pbbar'),pbtxt=document.getElementById('pbtxt');
      function setProgress(p){pbbar.style.width=p+'%';pbtxt.innerText=p+'%';}
      pbwrap.style.display='block';setProgress(0);
      s.innerText='Ziel: mods/'+name+'/ | Upload laeuft...';
      function nc(){
        if(idx>=tc){
          setProgress(100);
          fetch('/api/plugins/upload-finalize',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'name='+encodeURIComponent(name),cache:'no-store'})
            .then(r=>r.json()).then(d=>{
              if(d.ok){s.innerText='Fertig: '+d.message;loadPlugins();}
              else{s.innerText='Fehler: '+d.error;}
            }).catch(er=>{s.innerText='Fehler: '+er;});
          return;
        }
        const st=idx*1024,ed=Math.min(st+1024,b64.length),cd=b64.substring(st,ed);
        fetch('/api/plugins/upload-chunk',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'index='+idx+'&total='+tc+'&name='+encodeURIComponent(name)+'&data='+encodeURIComponent(cd),cache:'no-store'})
          .then(r=>r.json()).then(d=>{
            if(d.ok){idx++;setProgress(Math.round((idx/tc)*100));nc();}
            else{s.innerText='Fehler: '+d.error;}
          }).catch(er=>{s.innerText='Fehler: '+er;});
      }
      nc();
    }catch(er){s.innerText='Fehler: '+er;}
  };
  rd.readAsArrayBuffer(f);
}

let I18N={strings:{}};
function t(k,f){const v=I18N.strings[k];return typeof v==='string'?v:f;}
function applyI18n(){document.querySelectorAll('[data-i18n]').forEach(el=>{const v=t(el.getAttribute('data-i18n'),'');if(v)el.innerHTML=v;});document.title=t('plugins.pageTitle','Admin - Plugins');}
function loadI18n(){return fetch('/i18n-data',{cache:'no-store'}).then(r=>r.json()).then(d=>{I18N.strings=d.strings||{};applyI18n();}).catch(()=>{});}

loadI18n();
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


async def _handle_plugin_upload_chunk(writer, body_params):
    """Nimmt einen Base64-Chunk eines hochgeladenen Plugin-ZIPs entgegen
    (gleiches Chunk-Prinzip wie upload_helpers.handle_upload_chunk() fuer
    Firmware-Uploads, aber bewusst eigenstaendig/unabhaengig von dessen OTA-
    Status, siehe PLUGIN_UPLOAD_STAGING_B64). Der Zielname wird beim ersten
    Chunk (index=0) aus body_params['name'] uebernommen (vom Frontend bereits
    aus dem ZIP-Dateinamen abgeleitet, siehe pluginNameFromZipFilename()) und
    zusaetzlich hier nochmal sanitisiert."""
    try:
        chunk_index = int(body_params.get("index", "-1"))
        total = int(body_params.get("total", "0"))
        chunk_data = body_params.get("data", "")

        if chunk_index == 0:
            name = _sanitize_plugin_name(body_params.get("name", ""))
            if not name:
                await _send_json(writer, {"ok": False, "error": "Ungueltiger Plugin-Name"}, "400 Bad Request")
                return
            _plugin_upload_state["total_chunks"] = total
            _plugin_upload_state["received_chunks"] = 0
            _plugin_upload_state["name"] = name
            try:
                os.remove(PLUGIN_UPLOAD_STAGING_B64)
            except Exception:
                pass

        if not _plugin_upload_state["name"]:
            await _send_json(writer, {"ok": False, "error": "Kein Upload gestartet (erster Chunk fehlt)"}, "400 Bad Request")
            return

        if chunk_data:
            with open(PLUGIN_UPLOAD_STAGING_B64, "a") as f:
                f.write(chunk_data)
            _plugin_upload_state["received_chunks"] += 1

        await _send_json(writer, {"ok": True})
    except Exception as e:
        debug_log("[PLUGIN UPLOAD] Chunk-Fehler: {}".format(e))
        _plugin_upload_state["total_chunks"] = 0
        _plugin_upload_state["received_chunks"] = 0
        _plugin_upload_state["name"] = ""
        await _send_json(writer, {"ok": False, "error": str(e)[:150]}, "400 Bad Request")
    gc.collect()


def _reset_plugin_upload_state():
    _plugin_upload_state["total_chunks"] = 0
    _plugin_upload_state["received_chunks"] = 0
    _plugin_upload_state["name"] = ""
    for path in (PLUGIN_UPLOAD_STAGING_B64, PLUGIN_UPLOAD_STAGING_BIN):
        try:
            os.remove(path)
        except Exception:
            pass


async def _handle_plugin_upload_finalize(writer):
    """Dekodiert die fertig empfangene Base64-Staging-Datei zu einer echten
    ZIP-Binaerdatei (safe_base64_file_to_file(), gleiche Funktion wie beim
    Firmware-Upload) und entpackt sie ueber zip_helpers.extract_plugin_zip()
    nach mods/<name>/. Prueft ANSCHLIESSEND ueber plugin_manager.list_plugins(),
    ob das frisch entpackte Plugin auch tatsaechlich aktiv gelaufen ist -
    zip_helpers.extract_plugin_zip() kann erfolgreich Dateien schreiben, aber
    plugin_manager.load_single_plugin() trotzdem has_error=True setzen (z.B.
    Syntaxfehler/Exception im hochgeladenen Code, siehe plugin_manager.py's
    Crash-Isolation) - OHNE diese Nachpruefung wuerde die Antwort faelschlich
    "ok": true melden, obwohl das Plugin in Wahrheit deaktiviert/abgestuerzt
    dabei blieb. Raeumt IMMER auf (finally), egal ob Erfolg oder Fehler,
    damit ein fehlgeschlagener Upload keine Staging-Reste hinterlaesst."""
    import plugin_manager
    import zip_helpers

    name = _plugin_upload_state["name"]
    try:
        if not name:
            raise Exception("Kein Upload gestartet")
        if _plugin_upload_state["received_chunks"] != _plugin_upload_state["total_chunks"]:
            raise Exception("Upload unvollstaendig ({}/{} Chunks)".format(
                _plugin_upload_state["received_chunks"], _plugin_upload_state["total_chunks"]))

        decode_ok = safe_base64_file_to_file(PLUGIN_UPLOAD_STAGING_B64, PLUGIN_UPLOAD_STAGING_BIN)
        if decode_ok is not True:
            raise Exception("Base64-Dekodierung fehlgeschlagen: {}".format(decode_ok))

        zip_helpers.extract_plugin_zip(PLUGIN_UPLOAD_STAGING_BIN, name)

        installed = None
        for plugin in plugin_manager.list_plugins():
            if plugin["name"] == name:
                installed = plugin
                break

        if installed is not None and installed["has_error"]:
            debug_log("[PLUGIN UPLOAD] '{}' installiert, aber Aktivierung fehlgeschlagen: {}".format(
                name, installed["error_message"]))
            await _send_json(writer, {
                "ok": False,
                "error": "Dateien wurden nach mods/{}/ geschrieben, aber das Plugin konnte nicht geladen werden: {}".format(
                    name, installed["error_message"]),
            }, "500 Internal Server Error")
        else:
            debug_log("[PLUGIN UPLOAD] '{}' erfolgreich aus ZIP installiert".format(name))
            await _send_json(writer, {"ok": True, "message": "Plugin '{}' installiert (mods/{}/)".format(name, name)})
    except Exception as e:
        debug_log("[PLUGIN UPLOAD] Finalize-Fehler ({}): {}".format(name, e))
        await _send_json(writer, {"ok": False, "error": str(e)[:200]}, "500 Internal Server Error")
    finally:
        _reset_plugin_upload_state()
        gc.collect()


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

    if request_path == "/api/plugins/upload-chunk" and request_method == "POST":
        await _handle_plugin_upload_chunk(writer, body_params)
        return True

    if request_path == "/api/plugins/upload-finalize" and request_method == "POST":
        await _handle_plugin_upload_finalize(writer)
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

    if request_path.startswith("/api/plugin-ui/"):
        name = request_path[len("/api/plugin-ui/"):]
        schema = plugin_manager.get_ui_schema(name)
        if schema is None:
            await _send_json(writer, {"ok": False, "error": "Kein natives UI-Schema fuer dieses Plugin"}, "404 Not Found")
        else:
            await _send_json(writer, {"ok": True, "schema": schema})
        return True

    return False
