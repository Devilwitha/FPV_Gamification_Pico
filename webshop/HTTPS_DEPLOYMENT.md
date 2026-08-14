# HTTPS-Deployment fuer den Webshop

Der Flask-Webshop holt sich **kein** Let's-Encrypt-Zertifikat selbst. Die TLS-
Terminierung muss vor der App passieren, zum Beispiel mit **Caddy**. Caddy
fordert das Zertifikat automatisch bei Let's Encrypt an und leitet die
entschluesselten Requests intern an Flask auf `127.0.0.1:5000` weiter.

Im Repo liegen dafuer jetzt direkt nutzbare Produktionsdateien:

- `webshop/Caddyfile.example` fuer `bollisoft.ch`
- `webshop/fpv-webshop.service` als `systemd`-Unit
- `webshop/serve_production.py` als Waitress-Entrypoint

Zusatz: Der Link `https://bollisoft.ch/coverstore-maker/` wird per Caddy intern
an `127.0.0.1:9090` weitergeleitet.

## Voraussetzungen

- Eine oeffentlich erreichbare Domain, hier: `bollisoft.ch`
- DNS-A/AAAA-Record zeigt auf deinen Server
- Port `80` und `443` sind von aussen erreichbar
- Flask laeuft lokal auf `127.0.0.1:5000`
- Caddy ist installiert

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