"""Baut den Propwash FPV-Simulator aus den Quelldateien in diesem Ordner.

Erzeugt zwei Dateien:

- ../fpv-simulator.html
  Version fuer die Webseite (/tx15-werkstatt/fpv-simulator.html) mit
  Hauptnavigation (/site-nav.js) und Uebersetzung (i18n-dom.js). three.js
  kommt aus vendor/ vom eigenen Server, nicht von einem CDN.
- offline/propwash-fpv-simulator.html
  Eigenstaendige Version zum Herunterladen: eine einzige Datei, die per
  Doppelklick auch ohne Internet laeuft (three.js als data:-URL eingebettet,
  da Browser ES-Module von file:// nicht nachladen).

Aufruf: python build.py (Python 3.8+, keine weiteren Pakete noetig).
Die Webseite packt den Ordner offline/ samt Quellen als ZIP
(/fpv-simulator.zip, siehe webshop/app.py).
"""

import base64
from pathlib import Path

HERE = Path(__file__).resolve().parent
TITLE = "<title>Propwash FPV-Simulator</title>\n"
WEB_HEAD = (
    TITLE
    + '<script src="/static/js/i18n-dom.js" data-bundle="tools"></script>\n'
    + '<script src="/site-nav.js" defer></script>\n'
    + '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
)
OFFLINE_HEAD = (
    "<!doctype html>\n<html lang=\"de\">\n<head>\n<meta charset=\"utf-8\">\n"
    + '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
    + TITLE
)


def _clean(text):
    """Keine Leerzeichen am Zeilenende, genau ein Zeilenumbruch am Dateiende."""
    return "\n".join(line.rstrip() for line in text.split("\n")).rstrip("\n") + "\n"


def build():
    source = (HERE / "simulator.src.html").read_text(encoding="utf-8")
    core = (HERE / "sim-core.js").read_text(encoding="utf-8")
    three = (HERE / "vendor" / "three.module.min.js").read_bytes()
    page = source.replace("/*SIM_CORE*/", core)
    if TITLE not in page:
        raise SystemExit("simulator.src.html: <title> fehlt am Dateianfang.")

    web = page.replace(TITLE, WEB_HEAD, 1).replace(
        "/*THREE_URL*/", "/tx15-werkstatt/fpv-simulator/vendor/three.module.min.js"
    )
    # Die Webseiten-Version nur im Repo bauen (im entpackten Download fehlen
    # /site-nav.js und der Server, dort ist nur die Offline-Datei sinnvoll).
    built = ["offline/propwash-fpv-simulator.html"]
    if HERE.parent.name == "drohnen-tools":
        (HERE.parent / "fpv-simulator.html").write_text(_clean(web), encoding="utf-8")
        built.insert(0, "../fpv-simulator.html")

    data_url = "data:text/javascript;base64," + base64.b64encode(three).decode("ascii")
    offline = page.replace(TITLE, "", 1).replace("/*THREE_URL*/", data_url)
    offline = OFFLINE_HEAD + "</head>\n<body>\n" + offline + "\n</body>\n</html>\n"
    out_dir = HERE / "offline"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "propwash-fpv-simulator.html").write_text(_clean(offline), encoding="utf-8")
    print("Gebaut: " + ", ".join(built))


if __name__ == "__main__":
    build()
