"""role_setup.py - Einmalige Ersteinrichtung: fragt beim allerersten Start
ab, ob dieser Pico als "Gamification Pico" (vom Piloten getragener Score-
Tracker) oder als "Gate/Hill Pico" (stationaerer King-of-the-Hill-/Race-
Tor-Anker) laufen soll.

Bewusst als eigenstaendiges, in sich geschlossenes Skript (wie recovery.py):
zu diesem Zeitpunkt steht noch nicht fest, welches Hauptskript (main.py
oder main_gatehill.py) ueberhaupt geladen werden soll - es gibt also keine
main.py-Routing-Tabelle, auf die aufgesetzt werden koennte. Die Seite ist
daher als Python-String eingebettet, kein index.html noetig (gleiches
Prinzip wie recovery.py's eingebettete OTA-Seite).

Nach der Auswahl wird die Rolle dauerhaft gespeichert
(boot_runtime.set_device_role()) und der Pico neu gestartet - boot.py
importiert beim naechsten Start dann direkt main.py bzw. main_gatehill.py,
ohne diese Seite erneut zu zeigen.
"""
import machine
import time
import network
import asyncio
import json
import gc
from hotspot_common import configure_hotspot, load_hotspot_config

try:
    import boot_runtime
except Exception:
    boot_runtime = None

ENABLE_SERIAL_DEBUG = True

_HOTSPOT_CONFIG = load_hotspot_config()
AP_SSID = _HOTSPOT_CONFIG["ssid"]
AP_PASSWORD = _HOTSPOT_CONFIG["password"]


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


def start_access_point():
    configure_hotspot(
        AP_SSID,
        AP_PASSWORD,
        debug_log=lambda m: debug_log(f"[AP] {m}"),
        serial_debug=ENABLE_SERIAL_DEBUG,
    )


# ==================== EINGEBETTETE ERSTEINRICHTUNGS-SEITE ====================
ROLE_PAGE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FPV Pico - Ersteinrichtung</title>
<style>
body{background:#0a0c12;color:#e8f0f8;margin:0;padding:12px;font-family:monospace}
h1{color:#f39c12;font-size:1.3em;margin:0 0 6px}
.sub{color:#9fb4cb;font-size:.85em;margin:0 0 18px;line-height:1.4}
.c{max-width:560px;margin:0 auto;background:#141b25;padding:20px;border:2px solid #f39c12;border-radius:8px}
.opt{display:block;background:#1a2637;border:1px solid #335174;border-radius:8px;padding:16px;margin:0 0 14px}
.opt h2{margin:0 0 6px;font-size:1.05em;color:#fff}
.opt p{margin:0;font-size:.82em;color:#9fb4cb;line-height:1.4}
.btnwrap{margin-top:10px}
.b{background:#f39c12;color:#1a1204;padding:9px 16px;border:0;border-radius:4px;cursor:pointer;font-weight:bold;font-family:monospace}
.b:hover{filter:brightness(1.08)}
.msg{margin-top:14px;font-size:.85em;color:#9fb4cb;min-height:1.3em}
</style>
</head>
<body>
<div class="c">
<h1>&#128295; Ersteinrichtung</h1>
<p class="sub">Dieser Pico weiss noch nicht, welche Rolle er spielt. Waehle unten aus - die Wahl wird dauerhaft gespeichert und der Pico startet danach automatisch mit der passenden Firmware. Diese Seite erscheint danach nicht mehr.</p>

<div class="opt">
<h2>&#127918; Gamification Pico</h2>
<p>Wird vom Piloten am Copter/Sender getragen: Trick-Erkennung, Highscore, Challenges, Infection-Modus, King-of-the-Hill-/Race-Spieler.</p>
<div class="btnwrap"><button class="b" onclick="choose('gamification')">Als Gamification Pico einrichten</button></div>
</div>

<div class="opt">
<h2>&#9971; Gate/Hill Pico</h2>
<p>Bleibt stationaer stehen und ist der King-of-the-Hill-Huegel oder ein Race-Tor (Tor A/Tor B) fuer andere Piloten.</p>
<div class="btnwrap"><button class="b" onclick="choose('gatehill')">Als Gate/Hill Pico einrichten</button></div>
</div>

<div id="msg" class="msg"></div>
</div>
<script>
function choose(role){
if(!confirm('Diesen Pico dauerhaft als "'+role+'" einrichten? Ein Neustart folgt automatisch.'))return;
const msg=document.getElementById('msg');
msg.innerText='Speichere...';
fetch('/set-role?role='+encodeURIComponent(role),{cache:'no-store'}).then(r=>r.json()).then(d=>{
if(d.ok){msg.innerText='Gespeichert. Pico startet neu...';}
else{msg.innerText='Fehler: '+(d.error||'unbekannt');}
}).catch(e=>{msg.innerText='Fehler: '+e;});
}
</script>
</body>
</html>
"""


async def send_role_page(writer):
    body = ROLE_PAGE.encode('utf-8')
    writer.write(b'HTTP/1.1 200 OK\r\n')
    writer.write(b'Content-Type: text/html\r\n')
    writer.write(b'Content-Length: ' + str(len(body)).encode() + b'\r\n')
    writer.write(b'Connection: close\r\n\r\n')
    writer.write(body)
    await writer.drain()


def _json_response(writer, status_line, data):
    body = json.dumps(data).encode('utf-8')
    writer.write(b'HTTP/1.1 ' + status_line.encode() + b'\r\n')
    writer.write(b'Content-Type: application/json\r\n')
    writer.write(b'Cache-Control: no-store\r\n')
    writer.write(b'Content-Length: ' + str(len(body)).encode() + b'\r\n')
    writer.write(b'Connection: close\r\n\r\n')
    writer.write(body)


async def handle_client(reader, writer):
    try:
        request_line = await reader.readline()
        if not request_line:
            return

        request = request_line.decode('utf-8')
        parts = request.split(' ')
        request_target = parts[1] if len(parts) >= 2 else '/'

        if ENABLE_SERIAL_DEBUG:
            print(f"[DEBUG] [{time.ticks_ms() // 1000}s] [HTTP] {request_target}")

        if '?' in request_target:
            request_path, query_string = request_target.split('?', 1)
        else:
            request_path, query_string = request_target, ''

        # Restliche Header verwerfen - es werden keine Requests mit Body erwartet.
        while True:
            line = await reader.readline()
            if line == b'\r\n' or line == b'\n' or not line:
                break

        if request_path == '/set-role':
            role = ''
            for pair in query_string.split('&'):
                if pair.startswith('role='):
                    role = pair[5:]
                    break
            role = role.strip().lower()

            if role not in ('gamification', 'gatehill'):
                _json_response(writer, '400 Bad Request', {"ok": False, "error": "Ungueltige Rolle"})
            else:
                saved = False
                if boot_runtime is not None:
                    try:
                        saved = boot_runtime.set_device_role(role)
                    except Exception:
                        saved = False

                if saved:
                    _json_response(writer, '200 OK', {"ok": True, "role": role})
                    try:
                        await writer.drain()
                    except Exception:
                        pass
                    await asyncio.sleep_ms(1500)
                    debug_log(f"Rolle gespeichert ({role}), starte machine.reset()...")
                    machine.reset()
                else:
                    _json_response(writer, '500 Internal Server Error', {"ok": False, "error": "Speichern fehlgeschlagen"})
        else:
            await send_role_page(writer)

        await writer.drain()
    except OSError as e:
        if len(e.args) > 0 and e.args[0] == 104:
            pass
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
    debug_log("Ersteinrichtung laeuft. WLAN verbinden und http://192.168.4.1 aufrufen.")
    while True:
        _boot_feed_watchdog()
        await asyncio.sleep_ms(1000)


def run():
    debug_log("role_setup.py wurde gestartet.")
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        debug_log("Ersteinrichtung manuell gestoppt.")


run()
