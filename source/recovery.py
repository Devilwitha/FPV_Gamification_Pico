"""
main2.py - FPV_Gamification_Pico RECOVERY-/OTA-ONLY-Firmware

Notfall-Skript fuer sehr alte oder kaputte Installationen: Startet NUR
den WLAN-Hotspot und eine minimale OTA-Update-Seite - keine Telemetrie,
keine Trick-Erkennung, kein Score-Tracking. Die komplette OTA-Seite ist
als Python-String in DIESEM Skript eingebettet (kein index.html /
admin_*.html noetig), damit das Update auch funktioniert, wenn auf dem
Pico gar keine oder nur sehr alte/kaputte Dateien liegen.

WARUM main2.py?
----------------
MicroPython fuehrt beim Booten IMMER "main.py" aus, niemals "main2.py".
Damit dieses Skript tatsaechlich laeuft, musst du es auf dem Pico
UMBENENNEN (oder gleich als "main.py" hochladen):

  1. Verbinde den Pico per USB mit Thonny.
  2. Lade main2.py auf den Pico hoch.
  3. Benenne die Datei auf dem Pico von "main2.py" in "main.py" um
     (das bisherige/alte main.py wird dabei ueberschrieben - falls du
     es behalten willst, vorher lokal sichern).
  4. Starte den Pico neu (Hardware-Reset). Jetzt laeuft der Recovery-
     Server statt der normalen Firmware.
  5. Verbinde dich mit dem WLAN (siehe AP_SSID/AP_PASSWORD unten) und
     rufe http://192.168.4.1 im Browser auf.
  6. Lade entweder ein einzelnes main.py/HTML hoch, oder - empfohlen -
     ein komplettes Firmware-Bundle "firmware.nbo" (siehe
     build_firmware.py). Das Bundle wird automatisch entpackt und
     ersetzt main.py + alle admin_*.html + index.html auf einmal.
  7. Sobald main.py Teil des Uploads ist, startet der Pico automatisch
     neu und bootet danach wieder ganz normal (jetzt mit der neuen
     Firmware, nicht mehr mit diesem Recovery-Skript).

Der OTA-Ablauf (Chunk-Upload, Firmware-Bundle-Entpacken) ist
identisch zum OTA-System der normalen Firmware in main.py.
"""
import machine
import time
import network
import asyncio
import json
import os
import gc
import struct
from hotspot_common import configure_hotspot, load_hotspot_config
from ota_helpers import (
    url_decode,
    parse_query,
    read_exact,
    safe_base64_file_to_file as _ota_safe_base64_file_to_file,
    apply_firmware_bundle as _ota_apply_firmware_bundle,
    apply_firmware_bundle_from_base64 as _ota_apply_firmware_bundle_from_base64,
)

try:
    import boot_runtime
except Exception:
    boot_runtime = None

# ==================== CONFIGURATION ====================
ENABLE_SERIAL_DEBUG = True

_HOTSPOT_CONFIG = load_hotspot_config()
AP_SSID = _HOTSPOT_CONFIG["ssid"]
AP_PASSWORD = _HOTSPOT_CONFIG["password"]
# =======================================================

OTA_STAGING_PATH = "ota_staging.tmp"
# Nur diese Dateien duerfen per OTA ueberschrieben werden (kein Path-Traversal,
# keine beliebigen Dateinamen vom Client) - identische Whitelist wie in main.py.
OTA_ALLOWED_TARGETS = (
    "boot.py", "recovery.py", "hotspot_common.py", "hotspot.conf", "boot_runtime.py",
    "ota_helpers.py", "infection_mode.py",
    "main.py", "index.html",
    "admin_dashboard.html", "admin_update.html", "admin_simulate.html",
    "admin_profiles.html", "admin_system.html", "admin_infection.html", "infection_view.html",
    "firmware_version.txt",
)
# Spezial-Ziel: komplettes Firmware-Bundle (siehe build_firmware.py), das
# mehrere der obigen Dateien in einem Rutsch aktualisiert.
OTA_BUNDLE_TARGET = "firmware.nbo"
OTA_BUNDLE_MAGIC = b"FPVBNDL1"

ota_total_chunks = 0
ota_received_chunks = 0
ota_target_file = "main.py"
ota_update_active = False


def debug_log(message):
    if ENABLE_SERIAL_DEBUG:
        print(f"[DEBUG] [{time.ticks_ms() // 1000}s] {message}")


def _boot_feed_watchdog():
    if boot_runtime is None:
        return
    try:
        boot_runtime.feed_wdt()
    except Exception:
        pass


# url_decode, parse_query, read_exact sind jetzt in ota_helpers.py (siehe
# Import oben) - gemeinsam mit main.py genutzt statt manuell dupliziert.
# Die folgenden zwei Funktionen sind duenne Wrapper, die recovery.py's
# eigenes debug_log() als log-Callback durchreichen.

def safe_base64_file_to_file(input_file, output_file):
    return _ota_safe_base64_file_to_file(input_file, output_file, log=debug_log, feed_wdt=_boot_feed_watchdog)


def apply_firmware_bundle(bundle_path):
    """Entpackt ein per build_firmware.py erzeugtes Firmware-Bundle
    (firmware.nbo) - duenner Wrapper um ota_helpers.apply_firmware_bundle()."""
    return _ota_apply_firmware_bundle(bundle_path, OTA_ALLOWED_TARGETS, OTA_BUNDLE_MAGIC, log=debug_log, feed_wdt=_boot_feed_watchdog)


def apply_firmware_bundle_from_base64(base64_path):
    """Wie apply_firmware_bundle(), entpackt aber direkt aus der noch
    base64-kodierten Datei (z.B. 'update.pbp') ohne kompletten dekodierten
    Zwischenstand auf dem Flash - duenner Wrapper um
    ota_helpers.apply_firmware_bundle_from_base64()."""
    return _ota_apply_firmware_bundle_from_base64(base64_path, OTA_ALLOWED_TARGETS, OTA_BUNDLE_MAGIC, log=debug_log, feed_wdt=_boot_feed_watchdog)


def start_access_point():
    configure_hotspot(
        AP_SSID,
        AP_PASSWORD,
        debug_log=lambda m: debug_log(f"[AP] {m}"),
        serial_debug=ENABLE_SERIAL_DEBUG,
    )


# ==================== EINGEBETTETE OTA-SEITE ====================
# Bewusst als Python-String in main2.py eingebettet (kein index.html /
# admin_update.html noetig) - dieses Skript muss auch dann funktionieren,
# wenn auf dem Pico ausser main2.py gar keine Dateien mehr existieren.
RECOVERY_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FPV Pico - Recovery OTA</title>
<style>
body{background:#0a0c12;color:#e8f0f8;margin:0;padding:12px;font-family:monospace}
h1{color:#e74c3c;font-size:1.3em;margin:0 0 6px}
.c{max-width:640px;margin:0 auto;background:#141b25;padding:20px;border:2px solid #e74c3c;border-radius:8px}
.warn{background:#4d1a1a;color:#ff6b6b;border:1px solid #ff6b6b;border-radius:6px;padding:10px;font-size:.85em;margin-bottom:14px}
.s{background:#1a2637;padding:14px;border:1px solid #335174;border-radius:8px;margin:10px 0}
.s h2{color:#d8e5f4;margin:0 0 8px;font-size:.95em}
.st{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px solid rgba(255,255,255,.07);font-size:.85em}
.st:last-child{border-bottom:0}
input[type=file]{display:block;margin:10px 0;padding:8px;background:#0b1320;color:#e8f0f8;border:1px solid #335174;border-radius:4px;width:100%;box-sizing:border-box}
.b{background:#e74c3c;color:#fff;padding:10px 15px;border:0;border-radius:4px;cursor:pointer;margin:5px 2px 0 0;font-weight:bold;font-family:monospace}
.b.grey{background:#555}
.b.dis{background:#555;cursor:not-allowed;opacity:.7}
.b:hover{filter:brightness(1.1)}
.st2{margin:0;padding:10px;border-radius:4px;display:none;font-size:.85em}
.ok{background:#1a4d2e;color:#2ecc71;border:1px solid #2ecc71}
.err{background:#4d1a1a;color:#ff6b6b;border:1px solid #ff6b6b}
.pbwrap{display:none;margin:10px 0;height:20px;background:#0b1320;border:1px solid #335174;border-radius:4px;overflow:hidden;position:relative}
.pbbar{height:100%;width:0%;background:#e74c3c;transition:width .15s ease}
.pbtxt{position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;font-size:.72em;font-weight:bold;color:#fff;text-shadow:0 1px 2px #000}
.toggle{position:relative;display:inline-block;width:44px;height:24px;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:#335174;transition:.2s;border-radius:24px}
.slider:before{position:absolute;content:"";height:18px;width:18px;left:3px;bottom:3px;background:#e8f0f8;transition:.2s;border-radius:50%}
input:checked+.slider{background:#e74c3c}
input:checked+.slider:before{transform:translateX(20px)}
</style>
</head>
<body>
<div class="c">
<h1>&#128295; RECOVERY OTA</h1>
<div class="warn">Emergency mode: main2.py is running instead of normal firmware. No telemetry, no score tracking, OTA update only.</div>

<div class="s">
<h2>Status</h2>
<div class="st"><span>SSID</span><b>__SSID__</b></div>
<div class="st"><span>IP Address</span><b>__IP__</b></div>
<div class="st"><span>Free Memory</span><b>__MEMFREE__ KB</b></div>
</div>

<div class="s">
<h2>Upload Update</h2>
<div style="color:#9fb4cb;font-size:.78em;margin-bottom:6px">Allowed: main.py, index.html, admin_dashboard.html, admin_update.html, admin_simulate.html, admin_profiles.html, admin_system.html - or a full <b>firmware.nbo</b> bundle (recommended, see build_firmware.py)</div>
<input type="file" id="f" accept=".py,.html,.nbo">
<div id="fi" style="color:#9fb4cb;font-size:0.85em">Choose file...</div>
<div id="pbwrap" class="pbwrap"><div id="pbbar" class="pbbar"></div><div id="pbtxt" class="pbtxt"></div></div>
<div id="s" class="st2"></div>
<button class="b" onclick="u()">Upload</button>
<button class="b grey" onclick="r()">Restart</button>
<button id="ebtnMain" class="b dis" onclick="ewMain()" disabled>Emergency: delete main.py</button>
<div class="st" style="border-bottom:0;padding-top:8px">
<span>Arm emergency for main</span>
<label class="toggle"><input type="checkbox" id="earmMain" onchange="toggleEmergencyMainArm()"><span class="slider"></span></label>
</div>
<button id="ebtnBoot" class="b dis" onclick="ewBoot()" disabled>Emergency: delete boot.py</button>
<div class="st" style="border-bottom:0;padding-top:8px">
<span>Arm emergency for boot</span>
<label class="toggle"><input type="checkbox" id="earmBoot" onchange="toggleEmergencyBootArm()"><span class="slider"></span></label>
</div>
<div style="color:#9fb4cb;font-size:.78em">Use only for bootloop. Deletes exactly one file and reboots immediately.</div>
</div>
</div>
<script>
document.getElementById('f').addEventListener('change',function(e){
const f=e.target.files[0];
document.getElementById('fi').innerText=f?'File: '+f.name+' ('+(f.size/1024).toFixed(1)+' KB)':'Choose file...';
document.getElementById('pbwrap').style.display='none';
document.getElementById('pbbar').style.width='0%';
document.getElementById('pbtxt').innerText='';
document.getElementById('s').style.display='none';
});
function tgtFromName(n){
const a=['index.html','admin_dashboard.html','admin_update.html','admin_simulate.html','admin_profiles.html','admin_system.html'];
if(n&&n.toLowerCase().endsWith('.nbo'))return 'firmware.nbo';
return (n&&a.indexOf(n)>=0)?n:'main.py';
}
function u(){
const f=document.getElementById('f').files[0],s=document.getElementById('s');
if(!f){s.className='st2 err';s.innerText='No file selected!';s.style.display='block';return;}
const nl=f.name.toLowerCase();
if(!nl.endsWith('.py')&&!nl.endsWith('.html')&&!nl.endsWith('.nbo')){s.className='st2 err';s.innerText='Only .py, .html, or .nbo files are allowed!';s.style.display='block';return;}
const tgt=tgtFromName(f.name);
const isBinary=nl.endsWith('.nbo');
const rd=new FileReader();
rd.onload=function(e){
try{
let b;
if(isBinary){
b=new Uint8Array(e.target.result);
}else{
const ct=e.target.result,enc=new TextEncoder();
b=enc.encode(ct);
}
let bn='';
for(let i=0;i<b.length;i+=10240){const ch=b.slice(i,i+10240);for(let j=0;j<ch.length;j++)bn+=String.fromCharCode(ch[j]);}
const b64=btoa(bn),tc=Math.ceil(b64.length/1024);
let idx=0;
const pbwrap=document.getElementById('pbwrap'),pbbar=document.getElementById('pbbar'),pbtxt=document.getElementById('pbtxt');
function setProgress(p){pbbar.style.width=p+'%';pbtxt.innerText=p+'%';}
pbwrap.style.display='block';setProgress(0);
s.innerText='Target: '+tgt+' | Upload in progress...';s.className='st2';s.style.display='block';
function nc(){
if(idx>=tc){
setProgress(100);
fetch('/finalize-upload',{cache:'no-store'}).then(r=>r.json()).then(d=>{
if(d.ok){s.className='st2 ok';s.innerText='Done: '+d.message+(d.restart?' Restart in progress...':' Please restart manually (Restart button) to boot normal firmware.');}
else{s.className='st2 err';s.innerText='Error: '+d.error;}
}).catch(er=>{s.className='st2 err';s.innerText='Error: '+er;});
return;
}
const st=idx*1024,ed=Math.min(st+1024,b64.length),cd=b64.substring(st,ed);
fetch('/upload-chunk',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'index='+idx+'&total='+tc+'&target='+encodeURIComponent(tgt)+'&data='+encodeURIComponent(cd),cache:'no-store'}).then(r=>r.json()).then(d=>{
if(d.ok){idx++;setProgress(Math.round((idx/tc)*100));nc();}
else{s.className='st2 err';s.innerText='Error: '+d.error;}
}).catch(er=>{s.className='st2 err';s.innerText='Error: '+er;});
}
nc();
}catch(er){s.className='st2 err';s.innerText='Error: '+er;s.style.display='block';}
};
if(isBinary){rd.readAsArrayBuffer(f);}else{rd.readAsText(f);}
}
function r(){
if(confirm('Restart Pico now?')){
fetch('/restart-pico',{cache:'no-store'}).catch(()=>{});
}
}
function toggleEmergencyMainArm(){
const armed=document.getElementById('earmMain').checked;
const b=document.getElementById('ebtnMain');
b.disabled=!armed;
if(armed){b.classList.remove('dis');}
else{b.classList.add('dis');}
}
function toggleEmergencyBootArm(){
const armed=document.getElementById('earmBoot').checked;
const b=document.getElementById('ebtnBoot');
b.disabled=!armed;
if(armed){b.classList.remove('dis');}
else{b.classList.add('dis');}
}
function ewMain(){
if(document.getElementById('ebtnMain').disabled)return;
if(!confirm('Execute emergency? main.py will be deleted and Pico reboots.'))return;
fetch('/emergency-delete-main?confirm=1',{cache:'no-store'}).catch(()=>{});
}
function ewBoot(){
if(document.getElementById('ebtnBoot').disabled)return;
if(!confirm('Execute emergency? boot.py will be deleted and Pico reboots.'))return;
fetch('/emergency-delete-boot?confirm=1',{cache:'no-store'}).catch(()=>{});
}
</script>
</body>
</html>
"""


async def send_recovery_page(writer):
    try:
        ip_addr = network.WLAN(network.AP_IF).ifconfig()[0]
    except Exception:
        ip_addr = "?"
    try:
        mem_free_kb = f"{gc.mem_free() / 1024:.1f}"
    except Exception:
        mem_free_kb = "?"

    body = (
        RECOVERY_PAGE_TEMPLATE
        .replace("__SSID__", AP_SSID)
        .replace("__IP__", ip_addr)
        .replace("__MEMFREE__", mem_free_kb)
    ).encode('utf-8')

    writer.write(b'HTTP/1.1 200 OK\r\n')
    writer.write(b'Content-Type: text/html\r\n')
    writer.write(b'Content-Length: ' + str(len(body)).encode() + b'\r\n')
    writer.write(b'Connection: close\r\n\r\n')
    writer.write(body)
    await writer.drain()


async def handle_client(reader, writer):
    global ota_total_chunks, ota_received_chunks, ota_update_active, ota_target_file
    try:
        request_line = await reader.readline()
        if not request_line:
            return

        request = request_line.decode('utf-8')
        parts = request.split(' ')
        request_method = parts[0] if len(parts) >= 1 else 'GET'
        request_target = parts[1] if len(parts) >= 2 else '/'

        if ENABLE_SERIAL_DEBUG:
            # Bewusst print() statt debug_log(): rein informativ, landet nie in einer Datei.
            print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [HTTP] {request_method} {request_target}")

        if '?' in request_target:
            request_path, query_string = request_target.split('?', 1)
        else:
            request_path, query_string = request_target, ''

        content_length = 0
        while True:
            line = await reader.readline()
            if line == b'\r\n' or line == b'\n' or not line:
                break
            try:
                line_text = line.decode('utf-8').strip()
            except Exception:
                line_text = ''
            if line_text.lower().startswith('content-length:'):
                try:
                    content_length = int(line_text.split(':', 1)[1].strip())
                except Exception:
                    content_length = 0

        body_text = ""
        if request_method == 'POST' and content_length > 0:
            try:
                body_buffer = bytearray()
                bytes_remaining = content_length
                chunk_size = 2048
                while bytes_remaining > 0:
                    to_read = min(chunk_size, bytes_remaining)
                    chunk = await reader.read(to_read)
                    if not chunk:
                        break
                    body_buffer.extend(chunk)
                    bytes_remaining -= len(chunk)
                if body_buffer:
                    try:
                        body_text = body_buffer.decode('utf-8')
                    except Exception:
                        body_text = ""
            except Exception as e:
                debug_log(f"[HTTP] Fehler beim Lesen des POST Body: {e}")

        if request_path == '/upload-chunk' and request_method == 'POST':
            chunk_index_str = '-1'
            total_str = '0'
            target_str = 'main.py'
            if body_text:
                idx_pos = body_text.find('index=')
                if idx_pos >= 0:
                    idx_start = idx_pos + 6
                    idx_end = body_text.find('&', idx_start)
                    if idx_end < 0:
                        idx_end = len(body_text)
                    chunk_index_str = url_decode(body_text[idx_start:idx_end])

                total_pos = body_text.find('total=')
                if total_pos >= 0:
                    total_start = total_pos + 6
                    total_end = body_text.find('&', total_start)
                    if total_end < 0:
                        total_end = len(body_text)
                    total_str = url_decode(body_text[total_start:total_end])

                target_pos = body_text.find('target=')
                if target_pos >= 0:
                    target_start = target_pos + 7
                    target_end = body_text.find('&', target_start)
                    if target_end < 0:
                        target_end = len(body_text)
                    target_str = url_decode(body_text[target_start:target_end])

            chunk_data = ''
            if body_text:
                marker = '&data='
                pos = body_text.find(marker)
                if pos >= 0:
                    chunk_data = url_decode(body_text[pos + len(marker):])
                elif body_text.startswith('data='):
                    chunk_data = url_decode(body_text[5:])

            try:
                chunk_index = int(chunk_index_str)
                total = int(total_str)
                target_valid = True

                if chunk_index == 0:
                    if target_str not in OTA_ALLOWED_TARGETS and target_str != OTA_BUNDLE_TARGET:
                        target_valid = False
                    else:
                        ota_total_chunks = total
                        ota_received_chunks = 0
                        ota_update_active = True
                        ota_target_file = target_str
                        try:
                            os.remove('update.pbp')
                        except Exception:
                            pass
                        debug_log(f"[OTA] Chunk-Transfer gestartet: {total} Chunks erwartet, Ziel={target_str}")

                if not target_valid:
                    response = json.dumps({"ok": False, "error": f"Ungueltiges Ziel: {target_str}"}).encode('utf-8')
                    writer.write(b'HTTP/1.1 400 Bad Request\r\n')
                    writer.write(b'Content-Type: application/json\r\n')
                    writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                    writer.write(b'Connection: close\r\n\r\n')
                    writer.write(response)
                else:
                    if chunk_data:
                        with open('update.pbp', 'a') as f:
                            f.write(chunk_data)
                        ota_received_chunks += 1
                        debug_log(f"[OTA] Chunk {chunk_index + 1}/{total} empfangen ({len(chunk_data)} bytes)")

                    response = json.dumps({"ok": True, "message": f"Chunk {chunk_index + 1}/{total} gespeichert"}).encode('utf-8')
                    writer.write(b'HTTP/1.1 200 OK\r\n')
                    writer.write(b'Content-Type: application/json\r\n')
                    writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                    writer.write(b'Connection: close\r\n\r\n')
                    writer.write(response)

                    if chunk_index + 1 == total and ota_received_chunks == ota_total_chunks:
                        debug_log("[OTA] Alle Chunks empfangen, bitte /finalize-upload aufrufen")

                # Nach jedem Chunk aufraeumen (siehe main.py fuer Begruendung):
                # verhindert Heap-Fragmentierung ueber die ~200+ Requests
                # eines grossen Bundle-Uploads hinweg.
                gc.collect()

            except Exception as e:
                debug_log(f"[OTA CHUNK] Fehler: {e}")
                ota_update_active = False
                response = json.dumps({"ok": False, "error": str(e)}).encode('utf-8')
                writer.write(b'HTTP/1.1 400 Bad Request\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)

        elif request_path == '/finalize-upload':
            try:
                debug_log(f"[OTA] Finalisierung: {ota_received_chunks}/{ota_total_chunks} Chunks vorhanden")
                if ota_received_chunks != ota_total_chunks:
                    raise Exception(f"Incomplete upload: {ota_received_chunks}/{ota_total_chunks}")

                is_bundle = (ota_target_file == OTA_BUNDLE_TARGET)
                target = ota_target_file if (is_bundle or ota_target_file in OTA_ALLOWED_TARGETS) else "main.py"

                if is_bundle:
                    # Bundle wird DIREKT aus der noch base64-kodierten
                    # 'update.pbp' gestreamt entpackt, OHNE zuerst den
                    # kompletten Inhalt nach OTA_STAGING_PATH zu dekodieren -
                    # vermeidet, dass 'update.pbp' UND eine komplett
                    # dekodierte Bundle-Kopie gleichzeitig auf dem Flash
                    # liegen (OSError 28 / ENOSPC bei grossen Bundles).
                    extracted_files, needs_restart = apply_firmware_bundle_from_base64('update.pbp')
                    try:
                        os.remove('update.pbp')
                    except Exception:
                        pass
                    message = f"Firmware-Bundle angewendet: {len(extracted_files)} Datei(en) ersetzt ({', '.join(extracted_files)})"
                    if needs_restart:
                        message += " Starte Neustart..."
                else:
                    decode_ok = safe_base64_file_to_file('update.pbp', OTA_STAGING_PATH)
                    if decode_ok is not True:
                        raise Exception(f"Base64 Dekodierung fehlgeschlagen: {decode_ok}")

                    try:
                        os.remove('update.pbp')
                    except Exception:
                        pass

                    try:
                        staged_size = os.stat(OTA_STAGING_PATH)[6]
                        debug_log(f"[OTA] Staging-Datei: {staged_size} bytes (Ziel: {target})")
                    except Exception:
                        staged_size = 0

                    try:
                        os.remove(target)
                    except Exception:
                        pass

                    os.rename(OTA_STAGING_PATH, target)
                    debug_log(f"[OTA] Finale Datei gespeichert: {target} ({staged_size} bytes)")

                    needs_restart = (target == "main.py")
                    message = f"Update erfolgreich gespeichert: {target} ({staged_size} bytes)!"
                    if needs_restart:
                        message += " Starte Neustart..."

                response = json.dumps({"ok": True, "message": message, "restart": needs_restart}).encode('utf-8')
                writer.write(b'HTTP/1.1 200 OK\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)

                # Nach erfolgreichem Update den naechsten Main-Start wieder erlauben.
                if boot_runtime is not None:
                    try:
                        boot_runtime.request_main_retry_once()
                    except Exception:
                        pass

                ota_total_chunks = 0
                ota_received_chunks = 0
                ota_update_active = False
                try:
                    os.remove('update.pbp')
                except Exception:
                    pass

                try:
                    await writer.drain()
                except Exception:
                    pass

                if needs_restart:
                    await asyncio.sleep_ms(2000)
                    debug_log("[OTA] Starte machine.reset()...")
                    machine.reset()

            except Exception as e:
                debug_log(f"[OTA FINALIZE] Fehler: {str(e)[:100]}")
                response = json.dumps({"ok": False, "error": str(e)[:100]}).encode('utf-8')
                writer.write(b'HTTP/1.1 500 Internal Server Error\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
                ota_total_chunks = 0
                ota_received_chunks = 0
                ota_update_active = False
                try:
                    os.remove('update.pbp')
                except Exception:
                    pass
                try:
                    os.remove(OTA_STAGING_PATH)
                except Exception:
                    pass

        elif request_path == '/restart-pico':
            response = json.dumps({"ok": True, "message": "Pico is restarting..."}).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response)

            # Manueller Neustart aus Recovery soll ebenfalls einen Main-Test erlauben.
            if boot_runtime is not None:
                try:
                    boot_runtime.request_main_retry_once()
                except Exception:
                    pass

            try:
                await writer.drain()
            except Exception:
                pass
            await asyncio.sleep_ms(1000)
            debug_log("[RESTART] machine.reset() wird aufgerufen...")
            machine.reset()

        elif request_path == '/emergency-delete-main':
            confirm = parse_query(query_string).get('confirm', '')
            if confirm != '1' and body_text:
                confirm = parse_query(body_text).get('confirm', '')
            if confirm != '1':
                response = json.dumps({"ok": False, "error": "Confirmation missing"}).encode('utf-8')
                writer.write(b'HTTP/1.1 400 Bad Request\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
            else:
                deleted = []
                for path in ("main.py",):
                    try:
                        os.remove(path)
                        deleted.append(path)
                        debug_log("[EMERGENCY] Geloescht: " + path)
                    except Exception as e:
                        debug_log("[EMERGENCY] Konnte nicht loeschen: %s (%s)" % (path, e))

                response = json.dumps({
                    "ok": True,
                    "message": "Emergency executed (main.py). Restart in progress.",
                    "deleted": deleted,
                }).encode('utf-8')
                writer.write(b'HTTP/1.1 200 OK\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)

                try:
                    await writer.drain()
                except Exception:
                    pass
                await asyncio.sleep_ms(500)
                machine.reset()

        elif request_path == '/emergency-delete-boot':
            confirm = parse_query(query_string).get('confirm', '')
            if confirm != '1' and body_text:
                confirm = parse_query(body_text).get('confirm', '')
            if confirm != '1':
                response = json.dumps({"ok": False, "error": "Confirmation missing"}).encode('utf-8')
                writer.write(b'HTTP/1.1 400 Bad Request\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)
            else:
                deleted = []
                for path in ("boot.py",):
                    try:
                        os.remove(path)
                        deleted.append(path)
                        debug_log("[EMERGENCY] Geloescht: " + path)
                    except Exception as e:
                        debug_log("[EMERGENCY] Konnte nicht loeschen: %s (%s)" % (path, e))

                response = json.dumps({
                    "ok": True,
                    "message": "Emergency executed (boot.py). Restart in progress.",
                    "deleted": deleted,
                }).encode('utf-8')
                writer.write(b'HTTP/1.1 200 OK\r\n')
                writer.write(b'Content-Type: application/json\r\n')
                writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
                writer.write(b'Connection: close\r\n\r\n')
                writer.write(response)

                try:
                    await writer.drain()
                except Exception:
                    pass
                await asyncio.sleep_ms(500)
                machine.reset()

        elif request_path == '/system-info':
            try:
                mem_free = gc.mem_free()
            except Exception:
                mem_free = -1
            try:
                mem_alloc = gc.mem_alloc()
            except Exception:
                mem_alloc = -1
            try:
                ip_addr = network.WLAN(network.AP_IF).ifconfig()[0]
            except Exception:
                ip_addr = ""

            info_data = {
                "mem_free": mem_free,
                "mem_alloc": mem_alloc,
                "uptime_s": time.ticks_ms() // 1000,
                "ssid": AP_SSID,
                "ip": ip_addr,
                "recovery_mode": True,
                "ota_active": ota_update_active,
            }
            response_data = json.dumps(info_data).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Cache-Control: no-store, no-cache, must-revalidate\r\n')
            writer.write(b'Content-Length: ' + str(len(response_data)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response_data)

        else:
            await send_recovery_page(writer)

        await writer.drain()
    except OSError as e:
        if len(e.args) > 0 and e.args[0] == 104:
            if ENABLE_SERIAL_DEBUG:
                print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [WEB INFO] Client hat Verbindung geschlossen (ECONNRESET)")
        else:
            debug_log(f"[WEB ERROR] {e}")
    except Exception as e:
        debug_log(f"[WEB ERROR] {e}")
    finally:
        try:
            writer.close()
        except Exception:
            pass
        try:
            wait_closed = getattr(writer, 'wait_closed', None)
            if wait_closed is not None:
                await wait_closed()
        except Exception:
            pass
        await asyncio.sleep_ms(5)


async def main_async():
    start_access_point()
    await asyncio.start_server(handle_client, "0.0.0.0", 80)
    debug_log("Recovery-OTA-Server laeuft. WLAN verbinden und http://192.168.4.1 aufrufen.")
    while True:
        _boot_feed_watchdog()
        await asyncio.sleep_ms(1000)


def run():
    debug_log("main2.py (Recovery/OTA-only) wurde gestartet.")
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        debug_log("Recovery-Skript manuell gestoppt.")


run()
