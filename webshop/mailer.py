"""E-Mail-Versand des Webshops (derzeit: Admin-Benachrichtigungen).

Konfiguration per .env:

- ADMIN_EMAIL     - Empfaenger der Admin-Benachrichtigungen
- SMTP_HOST       - SMTP-Server, z. B. smtp.gmail.com
- SMTP_PORT       - Default 587 (STARTTLS); 465 = SSL
- SMTP_USER       - Anmeldename (leer = ohne Anmeldung)
- SMTP_PASSWORD   - Passwort bzw. App-Passwort
- SMTP_FROM       - Absender (Default: SMTP_USER)

Ohne ADMIN_EMAIL/SMTP_HOST wird nichts verschickt; send_admin_mail() meldet
das zurueck, damit der Aufrufer es anzeigen bzw. spaeter erneut versuchen kann.
"""

import os
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

SMTP_TIMEOUT_SECONDS = 20


def _config():
    return {
        "admin": os.environ.get("ADMIN_EMAIL", "").strip(),
        "host": os.environ.get("SMTP_HOST", "").strip(),
        "port": int(os.environ.get("SMTP_PORT", "").strip() or 587),
        "user": os.environ.get("SMTP_USER", "").strip(),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "sender": os.environ.get("SMTP_FROM", "").strip() or os.environ.get("SMTP_USER", "").strip(),
    }


def admin_mail_configured():
    config = _config()
    return bool(config["admin"] and config["host"] and config["sender"])


def send_admin_mail(subject, body):
    """Schickt eine Text-Mail an ADMIN_EMAIL. Liefert (True, None) oder (False, Fehlertext)."""
    config = _config()
    if not admin_mail_configured():
        return False, "E-Mail-Versand nicht eingerichtet (ADMIN_EMAIL, SMTP_HOST, SMTP_FROM in .env)."
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["sender"]
    message["To"] = config["admin"]
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=config["sender"].split("@")[-1] or None)
    message.set_content(body)
    try:
        context = ssl.create_default_context()
        if config["port"] == 465:
            server = smtplib.SMTP_SSL(config["host"], config["port"], timeout=SMTP_TIMEOUT_SECONDS, context=context)
        else:
            server = smtplib.SMTP(config["host"], config["port"], timeout=SMTP_TIMEOUT_SECONDS)
        with server:
            if config["port"] != 465:
                server.starttls(context=context)
            if config["user"]:
                server.login(config["user"], config["password"])
            server.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        return False, f"E-Mail konnte nicht gesendet werden: {exc}"
    return True, None
