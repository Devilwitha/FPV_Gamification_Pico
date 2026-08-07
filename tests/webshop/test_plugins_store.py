"""Tests fuer den Plugin-Store im Webshop (siehe app.py's PLUGINS_STORE_DIR/
_list_store_plugins()/admin_plugins_upload() - verteilt Mods aus
source/mods/ ueber static/plugins_store/<name>/ an den Pico und die
Android-App).
"""
import io
import json
import os
import zipfile

import pytest

# Gueltiger struktureller Platzhalter fuer eine .mpy-Datei - beginnt mit dem
# MicroPython-.mpy-Magic-Byte (0x4D = 'M'), siehe app.py's MPY_MAGIC_BYTE/
# _is_valid_mpy_bytes(). KEIN echtes kompiliertes Bytecode, nur genug, um
# die serverseitige Struktur-Plausibilitaetspruefung zu bestehen.
FAKE_MPY = b"\x4dmpy-fake-bytecode"


@pytest.fixture
def store_dir(webshop_app, tmp_path):
    """Biegt PLUGINS_STORE_DIR auf ein temporaeres Verzeichnis um - genau wie
    der bestehende LICENSES_DIR-Override in conftest.py's keypair-Fixture,
    damit Tests niemals in den echten webshop/static/plugins_store/ Ordner
    schreiben."""
    plugins_dir = tmp_path / "plugins_store"
    plugins_dir.mkdir()
    webshop_app.PLUGINS_STORE_DIR = str(plugins_dir)
    return plugins_dir


def _make_plugin_zip(entries):
    """Baut eine ZIP-Datei im Speicher aus {arcname: bytes_content}."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for arcname, content in entries.items():
            archive.writestr(arcname, content)
    buffer.seek(0)
    return buffer


def _login_admin(client):
    return client.post("/admin/login", data={"username": "admin", "password": "adminpass"})


def test_api_plugins_empty_when_store_dir_missing(client, webshop_app, tmp_path):
    webshop_app.PLUGINS_STORE_DIR = str(tmp_path / "does-not-exist")
    resp = client.get("/api/plugins")
    assert resp.status_code == 200
    assert resp.get_json() == {"plugins": []}


def test_list_store_plugins_reads_manifest_and_files(webshop_app, store_dir):
    plugin_dir = store_dir / "example_plugin"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(
        json.dumps({"name": "example_plugin", "version": "1.2.0", "author": "Team", "description": "Demo"}),
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text("def setup(context):\n    pass\n", encoding="utf-8")

    plugins = webshop_app._list_store_plugins()
    assert len(plugins) == 1
    assert plugins[0]["name"] == "example_plugin"
    assert plugins[0]["version"] == "1.2.0"
    assert plugins[0]["author"] == "Team"
    assert set(plugins[0]["files"]) == {"manifest.json", "main.py"}


def test_list_store_plugins_ignores_folder_without_manifest(webshop_app, store_dir):
    (store_dir / "not_a_plugin").mkdir()
    ((store_dir / "not_a_plugin") / "main.py").write_text("x = 1\n", encoding="utf-8")
    assert webshop_app._list_store_plugins() == []


def test_api_plugins_route_returns_store_contents(client, webshop_app, store_dir):
    plugin_dir = store_dir / "mymod"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(json.dumps({"name": "mymod", "version": "0.1.0"}), encoding="utf-8")
    (plugin_dir / "main.py").write_text("", encoding="utf-8")

    resp = client.get("/api/plugins")
    data = resp.get_json()
    assert data["plugins"][0]["name"] == "mymod"
    assert data["plugins"][0]["version"] == "0.1.0"


def test_plugins_store_page_renders_plugin_name(client, webshop_app, store_dir):
    plugin_dir = store_dir / "coolmod"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(
        json.dumps({"name": "coolmod", "version": "2.0.0", "description": "Ein cooles Mod"}), encoding="utf-8"
    )
    (plugin_dir / "main.py").write_text("", encoding="utf-8")

    resp = client.get("/plugins")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "coolmod" in body
    assert "Ein cooles Mod" in body


def test_admin_plugins_upload_requires_login(client, store_dir):
    zip_buffer = _make_plugin_zip({"manifest.json": json.dumps({"name": "x"}), "main.py": ""})
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_admin_plugins_upload_success_extracts_files(client, webshop_app, store_dir):
    _login_admin(client)
    zip_buffer = _make_plugin_zip(
        {
            "manifest.json": json.dumps({"name": "testmod", "version": "1.0.0"}),
            "main.mpy": FAKE_MPY,
        }
    )
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302

    target_dir = os.path.join(webshop_app.PLUGINS_STORE_DIR, "testmod")
    assert os.path.isfile(os.path.join(target_dir, "manifest.json"))
    assert os.path.isfile(os.path.join(target_dir, "main.mpy"))

    dashboard = client.get(resp.headers["Location"])
    assert "erfolgreich hochgeladen" in dashboard.data.decode("utf-8")


def test_admin_plugins_upload_flattens_subfolders(client, webshop_app, store_dir):
    """Unterordner in der ZIP (z.B. weil der Nutzer den ganzen Mod-Ordner statt
    nur dessen Inhalt gezippt hat) werden ignoriert - nur der Dateiname
    zaehlt, siehe admin_plugins_upload()'s os.path.basename()-Nutzung."""
    _login_admin(client)
    zip_buffer = _make_plugin_zip(
        {
            "my_mod/manifest.json": json.dumps({"name": "nested_mod", "version": "1.0.0"}),
            "my_mod/main.mpy": FAKE_MPY,
        }
    )
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    target_dir = os.path.join(webshop_app.PLUGINS_STORE_DIR, "nested_mod")
    assert os.path.isfile(os.path.join(target_dir, "manifest.json"))
    assert os.path.isfile(os.path.join(target_dir, "main.mpy"))


def test_admin_plugins_upload_rejects_zip_containing_py_files(client, webshop_app, store_dir):
    """Der Store verteilt Mods ausschliesslich als per mpy-cross vorkompilierte
    .mpy-Dateien (Quellcode-Schutz) - rohe .py-Dateien muessen abgelehnt
    werden, mit einem Hinweis auf mpy-cross/windows/source2/plugin_packager.py."""
    _login_admin(client)
    zip_buffer = _make_plugin_zip(
        {"manifest.json": json.dumps({"name": "rawsource"}), "main.py": "def setup(context):\n    pass\n"}
    )
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert webshop_app._list_store_plugins() == []
    dashboard = client.get(resp.headers["Location"])
    body = dashboard.data.decode("utf-8")
    assert "main.py" in body
    assert "mpy-cross" in body or "plugin_packager" in body


def test_admin_plugins_upload_rejects_missing_main_mpy(client, webshop_app, store_dir):
    _login_admin(client)
    zip_buffer = _make_plugin_zip({"manifest.json": json.dumps({"name": "incomplete"})})
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert webshop_app._list_store_plugins() == []
    dashboard = client.get(resp.headers["Location"])
    assert "manifest.json und main.mpy enthalten" in dashboard.data.decode("utf-8")


def test_admin_plugins_upload_rejects_invalid_manifest_name(client, webshop_app, store_dir):
    _login_admin(client)
    zip_buffer = _make_plugin_zip({"manifest.json": json.dumps({"name": "not a valid name!"}), "main.mpy": FAKE_MPY})
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert webshop_app._list_store_plugins() == []


def test_admin_plugins_upload_accepts_extra_non_py_files(client, webshop_app, store_dir):
    """Zusaetzliche Dateien wie eine eigene admin_*.html-Seite (siehe
    source/mods/shooter/) sind erlaubt und werden mit ausgeliefert."""
    _login_admin(client)
    zip_buffer = _make_plugin_zip(
        {
            "manifest.json": json.dumps({"name": "withpage", "version": "1.0.0"}),
            "main.mpy": FAKE_MPY,
            "admin_withpage.html": "<html>hi</html>",
        }
    )
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    target_dir = os.path.join(webshop_app.PLUGINS_STORE_DIR, "withpage")
    assert os.path.isfile(os.path.join(target_dir, "admin_withpage.html"))


def test_admin_plugins_upload_rejects_zip_slip_member(client, webshop_app, store_dir):
    """Ein Eintrag mit '..'-Segment darf NICHT zum Entpacken fuehren, selbst
    wenn manifest.json/main.mpy ebenfalls im Archiv vorhanden sind."""
    _login_admin(client)
    zip_buffer = _make_plugin_zip(
        {
            "manifest.json": json.dumps({"name": "sneaky"}),
            "main.mpy": FAKE_MPY,
            "../../evil.py": "print('pwned')",
        }
    )
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert webshop_app._list_store_plugins() == []
    assert not os.path.isdir(os.path.join(webshop_app.PLUGINS_STORE_DIR, "sneaky"))


def test_admin_plugins_upload_rejects_non_zip_extension(client, webshop_app, store_dir):
    _login_admin(client)
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (io.BytesIO(b"not a zip"), "mod.txt")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    dashboard = client.get(resp.headers["Location"])
    assert "Nur ZIP-Dateien" in dashboard.data.decode("utf-8")


def test_admin_plugins_upload_rejects_bad_zip_file(client, webshop_app, store_dir):
    _login_admin(client)
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (io.BytesIO(b"not actually a zip file"), "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    dashboard = client.get(resp.headers["Location"])
    assert "keine gültige ZIP-Datei" in dashboard.data.decode("utf-8")


def test_admin_plugins_upload_overwrites_existing_plugin(client, webshop_app, store_dir):
    """Erneuter Upload mit demselben manifest-Namen ueberschreibt die
    Dateien im bestehenden Ordner (siehe admin_plugins_upload()'s
    os.makedirs(..., exist_ok=True))."""
    _login_admin(client)
    first_zip = _make_plugin_zip({"manifest.json": json.dumps({"name": "mymod", "version": "1.0.0"}), "main.mpy": FAKE_MPY})
    client.post("/admin/plugins/upload", data={"plugin_zip": (first_zip, "mod.zip")}, content_type="multipart/form-data")

    second_zip = _make_plugin_zip({"manifest.json": json.dumps({"name": "mymod", "version": "2.0.0"}), "main.mpy": FAKE_MPY})
    client.post("/admin/plugins/upload", data={"plugin_zip": (second_zip, "mod.zip")}, content_type="multipart/form-data")

    plugins = webshop_app._list_store_plugins()
    assert len(plugins) == 1
    assert plugins[0]["version"] == "2.0.0"


def test_admin_plugins_upload_rejects_invalid_mpy_magic_byte(client, webshop_app, store_dir):
    """main.mpy muss mit dem MicroPython-.mpy-Magic-Byte (0x4D) beginnen -
    eine Datei, die nur die Endung traegt aber kein echtes .mpy-Format hat,
    wird abgelehnt (strukturelle Pruefung, siehe _is_valid_mpy_bytes())."""
    _login_admin(client)
    zip_buffer = _make_plugin_zip(
        {"manifest.json": json.dumps({"name": "badmagic"}), "main.mpy": b"\xffnotarealmpyfile"}
    )
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert webshop_app._list_store_plugins() == []
    dashboard = client.get(resp.headers["Location"])
    assert "gültige .mpy-Datei" in dashboard.data.decode("utf-8")


def test_admin_plugins_upload_rejects_empty_mpy_file(client, webshop_app, store_dir):
    _login_admin(client)
    zip_buffer = _make_plugin_zip({"manifest.json": json.dumps({"name": "emptymain"}), "main.mpy": b""})
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert webshop_app._list_store_plugins() == []


def test_admin_plugins_upload_may_overwrite_reserved_name(client, webshop_app, store_dir):
    """Der Admin darf (im Gegensatz zum Kunden-Upload) auch reservierte
    Standard-Mod-Namen wie 'shooter' aktualisieren."""
    _login_admin(client)
    zip_buffer = _make_plugin_zip({"manifest.json": json.dumps({"name": "shooter", "version": "9.9.9"}), "main.mpy": FAKE_MPY})
    resp = client.post(
        "/admin/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    plugins = {p["name"]: p for p in webshop_app._list_store_plugins()}
    assert plugins["shooter"]["version"] == "9.9.9"


def test_plugins_upload_requires_customer_login(client, store_dir):
    zip_buffer = _make_plugin_zip({"manifest.json": json.dumps({"name": "custommod"}), "main.mpy": FAKE_MPY})
    resp = client.post(
        "/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_plugins_upload_success_when_logged_in(client, webshop_app, store_dir):
    with client.session_transaction() as sess:
        sess["account_email"] = "kunde@example.com"

    zip_buffer = _make_plugin_zip(
        {"manifest.json": json.dumps({"name": "custommod", "version": "1.0.0"}), "main.mpy": FAKE_MPY}
    )
    resp = client.post(
        "/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/plugins")

    plugins = {p["name"]: p for p in webshop_app._list_store_plugins()}
    assert plugins["custommod"]["uploaded_by"] == "kunde@example.com"


def test_plugins_upload_rejects_reserved_name_for_customer(client, webshop_app, store_dir):
    with client.session_transaction() as sess:
        sess["account_email"] = "kunde@example.com"

    zip_buffer = _make_plugin_zip({"manifest.json": json.dumps({"name": "shooter"}), "main.mpy": FAKE_MPY})
    resp = client.post(
        "/plugins/upload",
        data={"plugin_zip": (zip_buffer, "mod.zip")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert webshop_app._list_store_plugins() == []
    dashboard = client.get(resp.headers["Location"])
    assert "Standard-Plugin" in dashboard.data.decode("utf-8")


def test_plugins_shooter_template_download_returns_zip_with_source_files(client):
    resp = client.get("/plugins/shooter-template.zip")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"

    import io as _io
    import zipfile as _zipfile

    with _zipfile.ZipFile(_io.BytesIO(resp.data)) as archive:
        names = set(archive.namelist())
    assert "main.py" in names
    assert "manifest.json" in names
    assert "ir_emitter.py" in names
    assert "ir_receiver.py" in names


def test_plugins_download_returns_zip_with_plugin_files(client, webshop_app, store_dir):
    plugin_dir = store_dir / "downloadable_mod"
    plugin_dir.mkdir()
    (plugin_dir / "manifest.json").write_text(
        json.dumps({"name": "downloadable_mod", "version": "1.0.0"}), encoding="utf-8"
    )
    (plugin_dir / "main.mpy").write_bytes(FAKE_MPY)

    resp = client.get("/plugins/downloadable_mod/download")
    assert resp.status_code == 200
    assert resp.headers["Content-Type"] == "application/zip"
    assert "downloadable_mod.zip" in resp.headers["Content-Disposition"]

    with zipfile.ZipFile(io.BytesIO(resp.data)) as archive:
        names = set(archive.namelist())
    assert names == {"manifest.json", "main.mpy"}


def test_plugins_download_returns_404_for_unknown_plugin(client, store_dir):
    resp = client.get("/plugins/does-not-exist/download")
    assert resp.status_code == 404
