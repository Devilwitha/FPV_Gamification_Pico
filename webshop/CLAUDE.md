# CLAUDE.md — Regeln für dieses Projekt (webshop/)

Diese Datei enthält verbindliche Regeln für die Arbeit an diesem Webshop-Projekt.
Sie gelten für Menschen und für KI-Assistenten (Claude Code) gleichermaßen.

## 1. Sprache

- Sämtliche Kommunikation, Commit-Nachrichten, Code-Kommentare und Dokumentation
  in diesem Projekt sind auf **Deutsch** zu verfassen.
- Ausnahme: Bezeichner (Variablen-, Funktions-, Klassennamen) bleiben in Englisch,
  da dies dem üblichen Python-/Web-Standard entspricht.

## 2. Vollständigkeit des Codes

- Es wird **immer vollständiger, lauffähiger Code** geliefert.
- Keine Platzhalter wie `// TODO`, `# TODO`, `...`, `pass  # implement later`
  oder ähnliche Abkürzungen.
- Keine Dummy- oder Mock-Implementierungen für sicherheitsrelevante oder
  zahlungsrelevante Funktionen. Zahlungsabwicklung (Stripe, PayPal) muss immer
  über die echten, offiziellen SDKs/APIs erfolgen.
- Wenn eine Funktion nicht vollständig umgesetzt werden kann, wird dies explizit
  im Chat kommuniziert — niemals stillschweigend als Lücke im Code hinterlassen.
- **Ausnahme (lokaler Dev-Schalter):** Die Umgebungsvariable `DUMMY_MODE`
  (Default: `false`) schaltet ausschließlich für lokale Entwicklung/Tests einen
  zusätzlichen "Dummy-Kauf simulieren"-Button frei, der Stripe/PayPal komplett
  umgeht. Er ersetzt oder verändert die echten Zahlungspfade nicht (die bleiben
  unverändert vollständig implementiert), ist standardmäßig deaktiviert und darf
  in Produktion (`DOMAIN_URL` != localhost o.ä.) niemals aktiv sein. Diese
  Ausnahme gilt ausschließlich für `DUMMY_MODE` — für alle anderen Funktionen
  bleibt die Mock-Verbot-Regel oben uneingeschränkt gültig.

## 3. Formatierung

- **Python**: 4 Leerzeichen Einrückung, PEP 8-konform, keine Tabs.
- **HTML / CSS / JavaScript**: 2 Leerzeichen Einrückung, konsistente
  Attribut-Reihenfolge, keine Inline-Styles außer für dynamisch generierte Werte.
- Zeilenumbrüche am Dateiende, keine trailing whitespaces.
- Konsistente Anführungszeichen: In Python doppelte Anführungszeichen (`"`),
  in HTML doppelte Anführungszeichen für Attribute.

## 4. Zahlungsanbindung

- **Stripe**: Ausschließlich über die offizielle `stripe` Python-Bibliothek und
  die Stripe Checkout Session API (`stripe.checkout.Session.create`).
  Der Nutzer wird direkt auf die von Stripe gehostete, gesicherte
  Checkout-Seite weitergeleitet.
- **PayPal**: Ausschließlich über die offizielle PayPal v2 REST-API
  (Orders API) sowie das offizielle PayPal JS-SDK
  (`https://www.paypal.com/sdk/js`) im Frontend. Server-seitige Endpunkte
  (`/api/paypal/create-order`, `/api/paypal/capture-order`) sprechen direkt
  mit der PayPal-API (OAuth2 Client-Credentials-Flow zur Token-Beschaffung).
- Geheime Schlüssel (Secret Keys, Client Secrets) werden **niemals** im
  Frontend-Code oder in Templates ausgegeben. Sie befinden sich ausschließlich
  serverseitig in `app.py` bzw. werden aus der `.env`-Datei geladen.
- Die `.env`-Datei enthält nur Platzhalterwerte im Repository und darf niemals
  echte Produktivschlüssel enthalten, die eingecheckt werden.

## 5. Projektstruktur

- Alle Templates liegen in `templates/`, alle statischen Assets in `static/`.
- Die Produktdaten liegen zentral als In-Memory-Dictionary in `app.py`.
- Preise werden intern immer in Cent (Integer) geführt, um Rundungsfehler bei
  Fließkommazahlen zu vermeiden. Die Anzeige im Frontend erfolgt formatiert
  in Euro (z. B. `49,99 €`).

## 6. Fehlerbehandlung

- Alle API-Aufrufe (Stripe, PayPal) sind mit sinnvoller Fehlerbehandlung
  versehen und liefern bei Fehlern aussagekräftige HTTP-Statuscodes sowie
  JSON-Fehlermeldungen an das Frontend zurück.
- Unbekannte Produkt-IDs führen zu einer HTTP-404-Antwort statt zu einem
  unbehandelten Ausnahmefehler.

## 7. Sicherheit

- Der Flask `SECRET_KEY` wird ausschließlich aus der Umgebungsvariable
  `FLASK_SECRET_KEY` geladen, niemals hartkodiert im Quellcode.
- Alle Preisberechnungen erfolgen serverseitig anhand der Produkt-ID —
  Preise werden niemals ungeprüft vom Client übernommen, um Preis-
  Manipulation zu verhindern.
