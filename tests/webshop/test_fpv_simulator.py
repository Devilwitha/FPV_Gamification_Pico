"""Tests fuer den Propwash FPV-Simulator auf der Webseite: Eintrag in den
Drohnen Tools (hub.py) und der Offline-Download /fpv-simulator.zip."""
import io
import zipfile


def test_home_lists_fpv_simulator(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"/tx15-werkstatt/fpv-simulator.html" in resp.data


def test_fpv_simulator_zip_contains_offline_file_and_sources(client):
    resp = client.get("/fpv-simulator.zip")
    assert resp.status_code == 200
    assert resp.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.data)) as archive:
        names = set(archive.namelist())
        assert {
            "propwash-fpv-simulator/propwash-fpv-simulator.html",
            "propwash-fpv-simulator/LIESMICH.md",
            "propwash-fpv-simulator/LICENSE-three.js.txt",
            "propwash-fpv-simulator/quellcode/sim-core.js",
            "propwash-fpv-simulator/quellcode/simulator.src.html",
            "propwash-fpv-simulator/quellcode/build.py",
            "propwash-fpv-simulator/quellcode/vendor/three.module.min.js",
        } <= names
        page = archive.read("propwash-fpv-simulator/propwash-fpv-simulator.html").decode("utf-8")
    # Offline-Datei: three.js eingebettet, kein CDN noetig
    assert "data:text/javascript;base64," in page
    assert "cdn.jsdelivr.net" not in page


def test_fpv_simulator_zip_missing_file_returns_404(webshop_app, client, monkeypatch):
    monkeypatch.setattr(
        webshop_app,
        "FPV_SIMULATOR_ZIP_FILES",
        webshop_app.FPV_SIMULATOR_ZIP_FILES + (("gibt-es-nicht.txt", "x.txt"),),
    )
    assert client.get("/fpv-simulator.zip").status_code == 404
