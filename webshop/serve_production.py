"""Produktions-Entrypoint fuer den Webshop hinter Caddy/Let's Encrypt."""

import os

from waitress import serve

from app import app


def _env_int(name, default):
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


if __name__ == "__main__":
    serve(
        app,
        host=os.environ.get("FLASK_HOST", "127.0.0.1"),
        port=_env_int("FLASK_PORT", 5000),
        threads=_env_int("WAITRESS_THREADS", 8),
    )