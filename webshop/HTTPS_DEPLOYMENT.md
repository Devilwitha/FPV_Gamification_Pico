# HTTPS-Deployment fuer den Webshop

Der Flask-Webshop holt sich **kein** Let's-Encrypt-Zertifikat selbst. Die TLS-
Terminierung muss vor der App passieren, zum Beispiel mit **Caddy**. Caddy
fordert das Zertifikat automatisch bei Let's Encrypt an und leitet die
entschluesselten Requests intern an Flask auf `127.0.0.1:5000` weiter.

> **Aktueller Produktivserver (Windows Server + IIS):** siehe Abschnitt
> [Windows-Server mit IIS](#windows-server-mit-iis) weiter unten. Die
> Caddy-/systemd-Anleitung gilt fuer einen Linux-Server.

Im Repo liegen dafuer jetzt direkt nutzbare Produktionsdateien:

- `webshop/Caddyfile.example` fuer `bollisoft.ch`
- `webshop/fpv-webshop.service` als `systemd`-Unit
- `webshop/serve_production.py` als Waitress-Entrypoint

Zusatz: Der Link `https://bollisoft.ch/coverstore-maker/` wird per Caddy intern
an `127.0.0.1:9090` weitergeleitet. Die statische TX15 Werkstatt aus
`drohnen-tools/` (Ordner in diesem Repo) wird unter
`https://bollisoft.ch/tx15-werkstatt/` ausgeliefert.

## Voraussetzungen

- Eine oeffentlich erreichbare Domain, hier: `bollisoft.ch`
- DNS-A/AAAA-Record zeigt auf deinen Server
- Port `80` und `443` sind von aussen erreichbar
- Flask laeuft lokal auf `127.0.0.1:5000`
- Caddy ist installiert
- Das Repo liegt unter `/opt/fpv-gamification-pico` (die Drohnen Tools im
  Unterordner `drohnen-tools/`)

Die Drohnen Tools liegen im Repo (`drohnen-tools/`) und kommen mit jedem
`git pull` des Repos mit - ein eigenes Klonen ist nicht mehr noetig.

Es ist kein separater App-Prozess notwendig. Caddy liefert die statischen
HTML-, JavaScript- und Asset-Dateien direkt aus; `.git` bleibt dabei gesperrt.

## App-Konfiguration

Die Datei `webshop/.env` ist bereits auf Reverse-Proxy-Betrieb vorbereitet:

- `DOMAIN_URL=https://bollisoft.ch`
- `TRUST_PROXY_COUNT=1`
- `SESSION_COOKIE_SECURE=true`
- `FLASK_HOST=127.0.0.1`
- `FLASK_PORT=5000`
- `FLASK_DEBUG=false`
- `DUMMY_MODE=false`

`TRUST_PROXY_COUNT=1` sorgt dafuer, dass Flask `X-Forwarded-Proto` und
`X-Forwarded-Host` vom Proxy korrekt auswertet. Dadurch funktionieren HTTPS-
Redirects, Session-Cookies und absolute Callback-URLs sauber hinter Caddy.

## Caddy einrichten

1. `webshop/Caddyfile.example` nach `/etc/caddy/Caddyfile` kopieren.
2. Python-Abhaengigkeiten installieren:

```bash
python -m pip install -r webshop/requirements.txt
```

3. `webshop/fpv-webshop.service` nach `/etc/systemd/system/fpv-webshop.service` kopieren und `WorkingDirectory`/`ExecStart` bei Bedarf an den echten Repo-Pfad anpassen.
4. Service aktivieren und starten:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now fpv-webshop
```

5. Caddy neu laden:

```bash
sudo systemctl reload caddy
```

6. Status pruefen:

```bash
sudo systemctl status fpv-webshop
sudo systemctl status caddy
```

## Beispiel-Caddyfile

```caddy
bollisoft.ch {
    encode zstd gzip

    handle_path /coverstore-maker/* {
        reverse_proxy 127.0.0.1:9090
    }

    redir /tx15-werkstatt /tx15-werkstatt/ 308

    handle_path /tx15-werkstatt/* {
        root * /opt/fpv-gamification-pico/drohnen-tools
        file_server {
            hide .git
        }
    }

    reverse_proxy 127.0.0.1:5000
}
```

Sobald die Domain korrekt auf den Server zeigt, beschafft und erneuert Caddy
das Let's-Encrypt-Zertifikat automatisch.

## Produktionsstart ohne Flask-Dev-Server

Fuer den Dienstbetrieb wird **Waitress** verwendet, nicht `app.run(...)` aus
dem Flask-Dev-Server. Der Entrypoint ist `webshop/serve_production.py` und
bindet ausschliesslich an `127.0.0.1:5000`, damit nur Caddy von aussen
terminiert.

## Windows-Server mit IIS

Auf dem Windows-Produktivserver startet **eine einzige Datei** alle Server:
`webshop/serve_production.py`.

| Dienst | intern | oeffentlich |
| --- | --- | --- |
| Webshop (Waitress) | `127.0.0.1:5000` | `https://www.bollisoft.ch/` |
| TX15 Werkstatt (statisch, im Webshop-Prozess) | `127.0.0.1:5000/tx15-werkstatt/` | `https://www.bollisoft.ch/tx15-werkstatt/` |
| Cover Store/Maker (ueberwachter Unterprozess) | `127.0.0.1:9090` | `https://www.bollisoft.ch/coverstore-maker/` |
| Blackbox Viewer (Node.js, ueberwachter Unterprozess) | `127.0.0.1:8787/blackbox-viewer/` | `https://www.bollisoft.ch/blackbox-viewer/` |

Cover Store und Blackbox Viewer werden nach einem Absturz automatisch neu
gestartet und enden immer zusammen mit `serve_production.py`.

Verzeichnisse (abweichende Pfade per `.env`):

- `drohnen-tools/` (Drohnen Tools, frueher eigenes Repo txtheme-maker),
  ueberschreibbar mit `TX15_WERKSTATT_DIR`
- `SteamOs_Pogram` (eigenes Repo neben diesem), Skript ueberschreibbar mit `COVERSTORE_SCRIPT`;
  `COVERSTORE_ENABLED=false` schaltet den Cover Store ab
- `blackbox-viewer/` (Blackbox Viewer, frueher eigenes Repo BlackboxViewer),
  ueberschreibbar mit `BLACKBOX_VIEWER_DIR`; Port mit `BLACKBOX_VIEWER_PORT`
  (Default 8787), `BLACKBOX_VIEWER_ENABLED=false` schaltet ihn ab. Benoetigt
  **Node.js LTS** (`C:\Program Files\nodejs\node.exe` oder im PATH, sonst
  `BLACKBOX_NODE`). Fehlt `dist/`, baut `serve_production.py` die App vor dem
  ersten Start selbst (`npm ci` + `npm run build`).

Einmalige Einrichtung (PowerShell als Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File webshop\install_webshop_autostart.ps1
```

Das Skript

1. registriert die Aufgabe **"Bollisoft Server"** (Start beim Systemstart als
   SYSTEM, ohne Zeitlimit),
2. deaktiviert die alte Einzelaufgabe "Bollisoft Cover Store",
3. traegt in der IIS-Site **"Bollisoft"** (IIS-Manager > Bollisoft >
   URL-Rewrite) die Proxy-Regeln `CoverStoreProxy`, `Tx15WerkstattProxy`,
   `BlackboxViewerProxy` und `ReverseProxyInboundRule1` ein (Sicherung als
   `web.config.bak`),
4. hebt fuer `/api/tx15-backups` das IIS-Upload-Limit an (Standard ~28 MB,
   Parameter `-BackupUploadLimit`, Default 300 MB).

### Backups des TX15 Backup Studios

Eingeloggte Konten speichern ihre SD-Karten-Backups ueber
`/api/tx15-backups` (siehe `webshop/tx15_backups.py`) auf dem Server:
ZIP-Dateien unter `tx15_backups/` im Repo-Ordner (nicht im Git, enthaelt
Kundendaten), Metadaten in `webshop.db`. Grenzen per `.env`:

- `TX15_BACKUP_MAX_BYTES` - je Backup (Default 250 MB)
- `TX15_BACKUP_QUOTA_BYTES` - je Konto (Default 1 GB)
- `TX15_BACKUP_MAX_COUNT` - Anzahl je Konto (Default 50)
- `TX15_BACKUP_DIR` - anderer Ablageordner

Themes (Theme Studio) und Lua-Skripte (Lua Studio) speichern eingeloggte
Konten ueber `/api/tx15-projects` (siehe `webshop/tx15_projects.py`):
Studio-Stand in `webshop.db`, ZIP unter `tx15_backups/projects/`. Grenzen:
`TX15_PROJECT_MAX_COUNT` (je Art und Konto, Default 100),
`TX15_PROJECT_MAX_STATE_BYTES` (Default 16 MB, enthaelt beim Lua Studio auch hochgeladene Dateien),
`TX15_PROJECT_MAX_ZIP_BYTES` (Default 20 MB). Alles zusammen erscheint auf
der Konto-Seite (`/account`, Abschnitt "Meine Drohnen Tools").

`tx15_backups/` (inklusive `projects/`) und `webshop.db` gehoeren ins
Server-Backup.

Die TLS-Zertifikate kommen dort von win-acme (Aufgabe "win-acme renew").

Neustart nach einem Update:

```powershell
Stop-ScheduledTask -TaskName 'Bollisoft Server'; Start-ScheduledTask -TaskName 'Bollisoft Server'
```

TX15 Werkstatt aktualisieren (kein Neustart noetig):

```powershell
git -C C:\Users\Administrator\Documents\GitHub\FPV_Gamification_Pico pull --ff-only
```

Blackbox Viewer aktualisieren (danach Neustart, siehe oben):

```powershell
cd C:\Users\Administrator\Documents\GitHub\FPV_Gamification_Pico\blackbox-viewer
git pull --ff-only; npm ci; npm run build
```

### TX15-Firmware im Backup Studio

`/api/tx15-firmware` (siehe `webshop/tx15_firmware.py`) holt die offiziellen
EdgeTX-Releases von GitHub (ab 2.12, aeltere enthalten keine TX15-Datei),
entnimmt dem Firmware-ZIP die `tx15-*.uf2` und liefert nur diese aus.
Zwischenspeicher: `tx15_backups/firmware-cache/` (darf jederzeit geloescht
werden). Installiert wird im Browser: Sender im Bootloader, Laufwerk
`EDGETX_UF2` waehlen - das Studio prueft vorher `INFO_UF2.TXT`
(`Board-ID: tx15`).

### Konto, Copter und Forum

- `/account/profil`: Benutzername, Profil, Passwort aendern;
  `/account/copter`: "Meine Copter" (siehe `webshop/konto.py`).
- `/forum`: oeffentlich lesbar, schreiben nur mit Login und Benutzername
  (siehe `webshop/forum.py`). Der Admin (`/admin/login`) verwaltet unter
  `/forum/verwaltung` die Ueberthemen, loescht/verschiebt/sperrt Themen und
  schreibt ohne eigenes Konto als "Bollisoft-Team".
- Admin-Dashboard: Anzahl registrierter Benutzer und wer in den letzten
  5 Minuten aktiv war (`accounts.last_seen_at`).
