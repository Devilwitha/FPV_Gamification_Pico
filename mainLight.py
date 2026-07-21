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

Der OTA-Ablauf (Chunk-Upload, Backup, Firmware-Bundle-Entpacken) ist
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

# ==================== CONFIGURATION ====================
ENABLE_SERIAL_DEBUG = True

AP_SSID = "FPV_Gamification_Pico"
AP_PASSWORD = "drohnenspiel"
# =======================================================

OTA_STAGING_PATH = "ota_staging.tmp"
# Nur diese Dateien duerfen per OTA ueberschrieben werden (kein Path-Traversal,
# keine beliebigen Dateinamen vom Client) - identische Whitelist wie in main.py.
OTA_ALLOWED_TARGETS = (
    "main.py", "index.html",
    "admin_dashboard.html", "admin_update.html", "admin_simulate.html",
    "admin_profiles.html", "admin_system.html",
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


def url_decode(value):
    value = value.replace('+', ' ')
    out = ""
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == '%' and i + 2 < len(value):
            hex_part = value[i + 1:i + 3]
            try:
                out += chr(int(hex_part, 16))
                i += 3
                continue
            except Exception:
                pass
        out += ch
        i += 1
    return out


def parse_query(query_string):
    params = {}
    if not query_string:
        return params
    pairs = query_string.split('&')
    for pair in pairs:
        if not pair:
            continue
        if '=' in pair:
            key, value = pair.split('=', 1)
        else:
            key, value = pair, ''
        params[url_decode(key)] = url_decode(value)
    return params


def safe_base64_file_to_file(input_file, output_file):
    """Dekodiert eine Base64-Textdatei streamend in eine Binaerdatei,
    ohne den kompletten Inhalt gleichzeitig im RAM zu halten."""
    try:
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
        carry = ""

        with open(input_file, 'r') as fin:
            with open(output_file, 'wb') as fout:
                while True:
                    chunk = fin.read(512)
                    if not chunk:
                        break

                    data = carry + chunk
                    usable_len = (len(data) // 4) * 4
                    to_decode = data[:usable_len]
                    carry = data[usable_len:]

                    out_bytes = bytearray()
                    for i in range(0, len(to_decode), 4):
                        group = to_decode[i:i + 4]
                        if len(group) < 4:
                            continue

                        nums = []
                        for c in group:
                            idx = alphabet.find(c)
                            nums.append(idx if idx >= 0 else 0)

                        b1 = (nums[0] << 2) | (nums[1] >> 4)
                        b2 = ((nums[1] & 0xF) << 4) | (nums[2] >> 2)
                        b3 = ((nums[2] & 0x3) << 6) | nums[3]

                        out_bytes.append(b1)
                        if group[2] != '=':
                            out_bytes.append(b2)
                        if group[3] != '=':
                            out_bytes.append(b3)

                    if out_bytes:
                        fout.write(out_bytes)

                if carry:
                    padding = (4 - len(carry) % 4) % 4
                    group = carry + ("=" * padding)
                    out_bytes = bytearray()
                    for i in range(0, len(group), 4):
                        g = group[i:i + 4]
                        if len(g) < 4:
                            continue

                        nums = []
                        for c in g:
                            idx = alphabet.find(c)
                            nums.append(idx if idx >= 0 else 0)

                        b1 = (nums[0] << 2) | (nums[1] >> 4)
                        b2 = ((nums[1] & 0xF) << 4) | (nums[2] >> 2)
                        b3 = ((nums[2] & 0x3) << 6) | nums[3]

                        out_bytes.append(b1)
                        if g[2] != '=':
                            out_bytes.append(b2)
                        if g[3] != '=':
                            out_bytes.append(b3)

                    if out_bytes:
                        fout.write(out_bytes)
        return True
    except Exception as e:
        debug_log(f"[BASE64-FILE-STREAM] Fehler: {e}")
        return False


def read_exact(f, n):
    """Liest exakt n Bytes aus einer binaer geoeffneten Datei (oder weniger
    bei EOF)."""
    data = bytearray()
    while len(data) < n:
        chunk = f.read(n - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def apply_firmware_bundle(bundle_path):
    """Entpackt ein per build_firmware.py erzeugtes Firmware-Bundle
    (firmware.nbo) und ersetzt jede enthaltene Datei einzeln auf dem
    Pico-Dateisystem (mit Backup, wie beim Einzeldatei-OTA-Update).
    Jeder Dateiname im Bundle wird gegen OTA_ALLOWED_TARGETS geprueft,
    bevor irgendetwas geschrieben wird."""
    extracted_files = []
    with open(bundle_path, 'rb') as f:
        magic = read_exact(f, len(OTA_BUNDLE_MAGIC))
        if magic != OTA_BUNDLE_MAGIC:
            raise Exception("Ungueltiges Firmware-Bundle (Magic-Header falsch)")

        count_bytes = read_exact(f, 4)
        if len(count_bytes) < 4:
            raise Exception("Bundle beschaedigt (Dateianzahl fehlt)")
        (file_count,) = struct.unpack('>I', count_bytes)

        for _ in range(file_count):
            name_len_bytes = read_exact(f, 4)
            if len(name_len_bytes) < 4:
                raise Exception("Bundle beschaedigt (Namenslaenge fehlt)")
            (name_len,) = struct.unpack('>I', name_len_bytes)

            name_bytes = read_exact(f, name_len)
            if len(name_bytes) < name_len:
                raise Exception("Bundle beschaedigt (Dateiname unvollstaendig)")
            filename = name_bytes.decode('utf-8')

            content_len_bytes = read_exact(f, 4)
            if len(content_len_bytes) < 4:
                raise Exception(f"Bundle beschaedigt (Inhaltslaenge fehlt: {filename})")
            (content_len,) = struct.unpack('>I', content_len_bytes)

            if filename not in OTA_ALLOWED_TARGETS:
                raise Exception(f"Datei im Bundle nicht erlaubt: {filename}")

            tmp_name = filename + ".bndl_tmp"
            remaining = content_len
            with open(tmp_name, 'wb') as out:
                while remaining > 0:
                    chunk = f.read(min(512, remaining))
                    if not chunk:
                        raise Exception(f"Bundle beschaedigt (Inhalt unvollstaendig: {filename})")
                    out.write(chunk)
                    remaining -= len(chunk)

            backup_path = "main_backup.py" if filename == "main.py" else (filename + ".bak")
            try:
                with open(filename, 'r') as old_f:
                    old_content = old_f.read()
                with open(backup_path, 'w') as bk:
                    bk.write(old_content)
            except Exception as e:
                debug_log(f"[OTA BUNDLE] Backup-Fehler ({filename}): {e}")

            try:
                os.remove(filename)
            except Exception:
                pass
            os.rename(tmp_name, filename)

            extracted_files.append(filename)
            debug_log(f"[OTA BUNDLE] Datei ersetzt: {filename} ({content_len} bytes)")

    needs_restart = "main.py" in extracted_files
    return extracted_files, needs_restart


def start_access_point():
    ap = network.WLAN(network.AP_IF)
    try:
        debug_log("[AP] Aktiviere Access Point")
        ap.active(True)
        time.sleep_ms(200)

        ssid_set = False
        for attempt in range(3):
            try:
                ap.config(essid=AP_SSID)
                ssid_set = True
                break
            except Exception as ssid_error:
                debug_log(f"[AP WARN] SSID-Setzen fehlgeschlagen (Versuch {attempt + 1}/3): {ssid_error}")
                time.sleep_ms(120)
        if not ssid_set:
            debug_log("[AP WARN] SSID konnte nicht gesetzt werden, AP laeuft mit Standardnamen weiter.")

        if AP_PASSWORD and len(AP_PASSWORD) >= 8:
            try:
                ap.config(password=AP_PASSWORD)
            except Exception as pw_error:
                debug_log(f"[AP WARN] Passwort-Konfiguration nicht verfuegbar, starte offenes WLAN: {pw_error}")

        ap.config(pm=0xa11140)
        ap.ifconfig(('192.168.4.1', '255.255.255.0', '192.168.4.1', '192.168.4.1'))

        debug_log("WLAN-Hotspot (Recovery) erfolgreich gestartet!")
        debug_log(f"SSID: {AP_SSID}")
        debug_log(f"Pico IP-Adresse (Im Browser eingeben): {ap.ifconfig()[0]}")
    except Exception as e:
        debug_log(f"[AP ERROR] Hotspot-Setup fehlgeschlagen: {e}")


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
.b:hover{filter:brightness(1.1)}
.st2{margin:0;padding:10px;border-radius:4px;display:none;font-size:.85em}
.ok{background:#1a4d2e;color:#2ecc71;border:1px solid #2ecc71}
.err{background:#4d1a1a;color:#ff6b6b;border:1px solid #ff6b6b}
</style>
</head>
<body>
<div class="c">
<h1>&#128295; RECOVERY OTA</h1>
<div class="warn">Notfall-Modus: Es laeuft main2.py statt der normalen Firmware. Keine Telemetrie, kein Score-Tracking - nur OTA-Update.</div>

<div class="s">
<h2>Status</h2>
<div class="st"><span>SSID</span><b>__SSID__</b></div>
<div class="st"><span>IP-Adresse</span><b>__IP__</b></div>
<div class="st"><span>Freier Speicher</span><b>__MEMFREE__ KB</b></div>
</div>

<div class="s">
<h2>Update hochladen</h2>
<div style="color:#9fb4cb;font-size:.78em;margin-bottom:6px">Erlaubt: main.py, index.html, admin_dashboard.html, admin_update.html, admin_simulate.html, admin_profiles.html, admin_system.html - oder ein komplettes <b>firmware.nbo</b> Bundle (empfohlen, siehe build_firmware.py)</div>
<input type="file" id="f" accept=".py,.html,.nbo">
<div id="fi" style="color:#9fb4cb;font-size:0.85em">Datei waehlen...</div>
<div id="s" class="st2"></div>
<button class="b" onclick="u()">Upload</button>
<button class="b grey" onclick="r()">Restart</button>
</div>
</div>
<script>
document.getElementById('f').addEventListener('change',function(e){
const f=e.target.files[0];
document.getElementById('fi').innerText=f?'Datei: '+f.name+' ('+(f.size/1024).toFixed(1)+' KB)':'Datei waehlen...';
});
function tgtFromName(n){
const a=['index.html','admin_dashboard.html','admin_update.html','admin_simulate.html','admin_profiles.html','admin_system.html'];
if(n&&n.toLowerCase().endsWith('.nbo'))return 'firmware.nbo';
return (n&&a.indexOf(n)>=0)?n:'main.py';
}
function u(){
const f=document.getElementById('f').files[0],s=document.getElementById('s');
if(!f){s.className='st2 err';s.innerText='Keine Datei!';s.style.display='block';return;}
const nl=f.name.toLowerCase();
if(!nl.endsWith('.py')&&!nl.endsWith('.html')&&!nl.endsWith('.nbo')){s.className='st2 err';s.innerText='Nur .py, .html oder .nbo Dateien erlaubt!';s.style.display='block';return;}
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
s.innerText='Ziel: '+tgt+' | Chunk 1/'+tc;s.className='st2';s.style.display='block';
function nc(){
if(idx>=tc){
fetch('/finalize-upload',{cache:'no-store'}).then(r=>r.json()).then(d=>{
if(d.ok){s.className='st2 ok';s.innerText='Fertig: '+d.message+(d.restart?' Neustart laeuft...':' Bitte manuell neu starten (Restart-Button), um in die normale Firmware zu booten.');}
else{s.className='st2 err';s.innerText='Fehler: '+d.error;}
}).catch(er=>{s.className='st2 err';s.innerText='Fehler: '+er;});
return;
}
const st=idx*1024,ed=Math.min(st+1024,b64.length),cd=b64.substring(st,ed);
fetch('/upload-chunk',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:'index='+idx+'&total='+tc+'&target='+encodeURIComponent(tgt)+'&data='+encodeURIComponent(cd),cache:'no-store'}).then(r=>r.json()).then(d=>{
if(d.ok){idx++;s.innerText='Ziel: '+tgt+' | Chunk '+(idx+1)+'/'+tc;nc();}
else{s.className='st2 err';s.innerText='Fehler: '+d.error;}
}).catch(er=>{s.className='st2 err';s.innerText='Fehler: '+er;});
}
nc();
}catch(er){s.className='st2 err';s.innerText='Fehler: '+er;s.style.display='block';}
};
if(isBinary){rd.readAsArrayBuffer(f);}else{rd.readAsText(f);}
}
function r(){
if(confirm('Pico jetzt neu starten?')){
fetch('/restart-pico',{cache:'no-store'}).catch(()=>{});
}
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

                decode_ok = safe_base64_file_to_file('update.pbp', OTA_STAGING_PATH)
                if not decode_ok:
                    raise Exception("Base64 Dekodierung fehlgeschlagen")

                try:
                    staged_size = os.stat(OTA_STAGING_PATH)[6]
                    debug_log(f"[OTA] Staging-Datei: {staged_size} bytes (Ziel: {target})")
                except Exception:
                    staged_size = 0

                if is_bundle:
                    extracted_files, needs_restart = apply_firmware_bundle(OTA_STAGING_PATH)
                    try:
                        os.remove(OTA_STAGING_PATH)
                    except Exception:
                        pass
                    message = f"Firmware-Bundle angewendet: {len(extracted_files)} Datei(en) ersetzt ({', '.join(extracted_files)})"
                    if needs_restart:
                        message += " Starte Neustart..."
                else:
                    backup_path = "main_backup.py" if target == "main.py" else (target + ".bak")
                    try:
                        with open(target, 'r') as f:
                            old_content = f.read()
                        with open(backup_path, 'w') as f:
                            f.write(old_content)
                        debug_log(f"[OTA] Backup erstellt: {backup_path} ({len(old_content)} bytes)")
                    except Exception as e:
                        debug_log(f"[OTA] Backup-Fehler ({target}): {e}")

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
            response = json.dumps({"ok": True, "message": "Pico startet neu..."}).encode('utf-8')
            writer.write(b'HTTP/1.1 200 OK\r\n')
            writer.write(b'Content-Type: application/json\r\n')
            writer.write(b'Content-Length: ' + str(len(response)).encode() + b'\r\n')
            writer.write(b'Connection: close\r\n\r\n')
            writer.write(response)
            try:
                await writer.drain()
            except Exception:
                pass
            await asyncio.sleep_ms(1000)
            debug_log("[RESTART] machine.reset() wird aufgerufen...")
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
        await asyncio.sleep_ms(1000)


def run():
    debug_log("main2.py (Recovery/OTA-only) wurde gestartet.")
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        debug_log("Recovery-Skript manuell gestoppt.")


run()
