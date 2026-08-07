"""Tests fuer windows/source2/plugin_packager.py - Paketieren (.py -> .mpy via
mpy-cross) und Hoch-/Herunterladen eines Mod-Ordners zum/vom Webshop-
Plugin-Store. Liegt bewusst weiterhin unter tests/tools/ (nicht
tests/windows/) - testet ausschliesslich die reinen Hilfsfunktionen, nicht
die Tk-GUI, siehe tests/tools/conftest.py fuer den sys.path-Eintrag auf
windows/source2/.

Netzwerkfunktionen (webshop_login/upload_plugin_zip/fetch_store_plugins/
download_plugin) werden ueber Fake-Objekte getestet statt gegen einen
echten Webshop-Server - gleiches Prinzip wie die Fake-Response-Objekte in
tests/webshop/*."""
import json
import os
import zipfile

import pytest


@pytest.fixture
def plugin_packager(deploy_mod, fresh_import):
    """Baut auf der bestehenden deploy_mod-Fixture auf (die ihrerseits die
    build_firmware-Sandbox nutzt, siehe tests/tools/conftest.py) - dadurch
    zeigt plugin_packager.deploy_mod.MODS_SOURCE_DIR auf eine tmp_path-Kopie
    von source/mods/, niemals auf das echte Repo."""
    return fresh_import("plugin_packager")


def test_pack_mod_to_zip_compiles_py_to_mpy_and_excludes_py_files(plugin_packager, tmp_path):
    output_path = str(tmp_path / "shooter.zip")
    result = plugin_packager.pack_mod_to_zip("shooter", output_path)

    assert result == output_path
    assert os.path.isfile(output_path)

    with zipfile.ZipFile(output_path) as archive:
        names = archive.namelist()

    assert "main.mpy" in names
    assert "manifest.json" in names
    assert "admin_shooter.html" in names
    assert not any(name.endswith(".py") for name in names)


def test_pack_mod_to_zip_raises_for_unknown_mod(plugin_packager, tmp_path):
    with pytest.raises(Exception, match="leer oder existiert nicht"):
        plugin_packager.pack_mod_to_zip("does-not-exist", str(tmp_path / "out.zip"))


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b""):
        self.status_code = status_code
        self._json_data = json_data
        self.content = content

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, login_ok=True, upload_ok=True):
        self.login_ok = login_ok
        self.upload_ok = upload_ok
        self.posts = []

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if url.endswith("/login"):
            return _FakeResponse(status_code=302 if self.login_ok else 200)
        if url.endswith("/plugins/upload"):
            return _FakeResponse(status_code=302 if self.upload_ok else 400)
        raise AssertionError(f"unerwartete URL: {url}")


def test_webshop_login_success(plugin_packager):
    session = _FakeSession(login_ok=True)
    plugin_packager.webshop_login(session, "http://example.com", "user@example.com", "secret")
    assert session.posts[0][0] == "http://example.com/login"
    assert session.posts[0][1]["data"] == {"email": "user@example.com", "password": "secret"}


def test_webshop_login_failure_raises(plugin_packager):
    session = _FakeSession(login_ok=False)
    with pytest.raises(Exception, match="Login fehlgeschlagen"):
        plugin_packager.webshop_login(session, "http://example.com", "user@example.com", "wrong")


def test_upload_plugin_zip_success(plugin_packager, tmp_path):
    zip_path = tmp_path / "mod.zip"
    zip_path.write_bytes(b"fake zip content")
    session = _FakeSession(upload_ok=True)
    plugin_packager.upload_plugin_zip(session, "http://example.com/", str(zip_path))
    assert session.posts[0][0] == "http://example.com/plugins/upload"


def test_upload_plugin_zip_failure_raises(plugin_packager, tmp_path):
    zip_path = tmp_path / "mod.zip"
    zip_path.write_bytes(b"fake zip content")
    session = _FakeSession(upload_ok=False)
    with pytest.raises(Exception, match="Upload fehlgeschlagen"):
        plugin_packager.upload_plugin_zip(session, "http://example.com", str(zip_path))


def test_fetch_store_plugins_returns_plugin_list(plugin_packager, monkeypatch):
    fake_plugins = [{"name": "demo", "version": "1.0.0", "files": ["manifest.json", "main.mpy"]}]

    def fake_get(url, timeout):
        assert url == "http://example.com/api/plugins"
        return _FakeResponse(status_code=200, json_data={"plugins": fake_plugins})

    monkeypatch.setattr(plugin_packager.requests, "get", fake_get)
    result = plugin_packager.fetch_store_plugins("http://example.com")
    assert result == fake_plugins


def test_download_plugin_writes_each_file(plugin_packager, monkeypatch, tmp_path):
    contents = {
        "manifest.json": json.dumps({"name": "demo"}).encode(),
        "main.mpy": b"\x4dfakebytecode",
    }

    def fake_get(url, timeout):
        filename = url.rsplit("/", 1)[-1]
        assert filename in contents
        return _FakeResponse(status_code=200, content=contents[filename])

    monkeypatch.setattr(plugin_packager.requests, "get", fake_get)

    target_dir = plugin_packager.download_plugin(
        "http://example.com", "demo", list(contents.keys()), str(tmp_path)
    )

    assert target_dir == os.path.join(str(tmp_path), "demo")
    for filename, data in contents.items():
        with open(os.path.join(target_dir, filename), "rb") as f:
            assert f.read() == data
