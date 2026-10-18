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
`github.com/Devilwitha/txtheme-maker` wird unter
`https://bollisoft.ch/tx15-werkstatt/` ausgeliefert.

## Voraussetzungen

- Eine oeffentlich erreichbare Domain, hier: `bollisoft.ch`
- DNS-A/AAAA-Record zeigt auf deinen Server
- Port `80` und `443` sind von aussen erreichbar
- Flask laeuft lokal auf `127.0.0.1:5000`
- Caddy ist installiert
- Das TX15-Werkstatt-Repository liegt unter `/srv/txtheme-maker`

Die TX15 Werkstatt wird einmalig so bereitgestellt:

```bash
sudo git clone https://github.com/Devilwitha/txtheme-maker.git /srv/txtheme-maker
sudo chown -R caddy:caddy /srv/txtheme-maker
```

Spaetere Aktualisierungen werden direkt aus dem Repository uebernommen:

```bash
sudo -u caddy git -C /srv/txtheme-maker pull --ff-only
```

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
        root * /srv/txtheme-maker
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

Der Cover Store wird nach einem Absturz automatisch neu gestartet und endet
immer zusammen mit `serve_production.py`.

Erwartete Verzeichnisse (neben diesem Repo im selben `GitHub`-Ordner,
abweichende Pfade per `.env`):

- `txtheme-maker` (`git clone https://github.com/Devilwitha/txtheme-maker.git`),
  ueberschreibbar mit `TX15_WERKSTATT_DIR`
- `SteamOs_Pogram`, Skript ueberschreibbar mit `COVERSTORE_SCRIPT`;
  `COVERSTORE_ENABLED=false` schaltet den Cover Store ab

Einmalige Einrichtung (PowerShell als Administrator):

```powershell
powershell -ExecutionPolicy Bypass -File webshop\install_webshop_autostart.ps1
```

Das Skript

1. registriert die Aufgabe **"Bollisoft Server"** (Start beim Systemstart als
   SYSTEM, ohne Zeitlimit),
2. deaktiviert die alte Einzelaufgabe "Bollisoft Cover Store",
3. traegt in der IIS-Site **"Bollisoft"** (IIS-Manager > Bollisoft >
   URL-Rewrite) die Proxy-Regeln `CoverStoreProxy`, `Tx15WerkstattProxy` und
   `ReverseProxyInboundRule1` ein (Sicherung als `web.config.bak`).

Die TLS-Zertifikate kommen dort von win-acme (Aufgabe "win-acme renew").

Neustart nach einem Update:

```powershell
Stop-ScheduledTask -TaskName 'Bollisoft Server'; Start-ScheduledTask -TaskName 'Bollisoft Server'
```

TX15 Werkstatt aktualisieren (kein Neustart noetig):

```powershell
git -C C:\Users\Administrator\Documents\GitHub\txtheme-maker pull --ff-only
```