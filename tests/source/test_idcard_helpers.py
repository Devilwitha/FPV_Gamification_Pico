import base64

import pytest

import idcard_helpers as ic


@pytest.fixture(autouse=True)
def _reset_upload_state():
    ic._reset_upload_state()
    yield
    ic._reset_upload_state()


def test_detect_format_png():
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
    path = "test.bin"
    with open(path, "wb") as f:
        f.write(png_bytes)
    assert ic._detect_format(path) == "png"


def test_detect_format_jpg():
    with open("test.bin", "wb") as f:
        f.write(b"\xff\xd8\xff\xe0" + b"\x00" * 20)
    assert ic._detect_format("test.bin") == "jpg"


def test_detect_format_pdf():
    with open("test.bin", "wb") as f:
        f.write(b"%PDF-1.4\n" + b"\x00" * 20)
    assert ic._detect_format("test.bin") == "pdf"


def test_detect_format_unknown():
    with open("test.bin", "wb") as f:
        f.write(b"not a real file")
    assert ic._detect_format("test.bin") is None


def test_detect_format_missing_file():
    assert ic._detect_format("does_not_exist.bin") is None


def test_get_existing_path_and_type_none_when_absent():
    assert ic._get_existing_path_and_type() == (None, None)


def test_get_existing_path_and_type_prefers_jpg_over_png_and_pdf():
    open(ic.FPV_AUSWEISS_JPG_PATH, "wb").close()
    open(ic.FPV_AUSWEISS_PNG_PATH, "wb").close()
    open(ic.FPV_AUSWEISS_PDF_PATH, "wb").close()
    assert ic._get_existing_path_and_type() == (ic.FPV_AUSWEISS_JPG_PATH, "image/jpeg")


def test_get_existing_path_and_type_falls_back_to_png_then_pdf():
    open(ic.FPV_AUSWEISS_PDF_PATH, "wb").close()
    assert ic._get_existing_path_and_type() == (ic.FPV_AUSWEISS_PDF_PATH, "application/pdf")

    open(ic.FPV_AUSWEISS_PNG_PATH, "wb").close()
    assert ic._get_existing_path_and_type() == (ic.FPV_AUSWEISS_PNG_PATH, "image/png")


def test_decode_b64_chunk_roundtrip():
    raw = b"hello world, this is binary-ish data \x00\x01\x02"
    encoded = base64.b64encode(raw).decode("ascii")
    assert ic._decode_b64_chunk(encoded) == raw


def test_format_storage_error_enospc():
    err = OSError(28, "No space left on device")
    assert "Kein Speicherplatz frei" in ic._format_storage_error(err)


def test_format_storage_error_generic_exception():
    err = ValueError("boom")
    assert ic._format_storage_error(err) == "boom"


class FakeWriter:
    def __init__(self):
        self.chunks = []

    def write(self, data):
        self.chunks.append(data)

    async def drain(self):
        pass

    @property
    def response(self):
        return b"".join(self.chunks)

    def json(self):
        import json
        body = self.response.split(b"\r\n\r\n", 1)[1]
        return json.loads(body)

    @property
    def status_line(self):
        return self.response.split(b"\r\n", 1)[0].decode()


@pytest.mark.asyncio
async def test_handle_status_no_file():
    writer = FakeWriter()
    await ic._handle_status(writer)
    data = writer.json()
    assert data == {"ok": True, "exists": False, "filename": "", "mime": "", "size": 0}


@pytest.mark.asyncio
async def test_handle_status_with_existing_file():
    content = b"\x89PNG\r\n\x1a\n" + b"x" * 100
    with open(ic.FPV_AUSWEISS_PNG_PATH, "wb") as f:
        f.write(content)
    writer = FakeWriter()
    await ic._handle_status(writer)
    data = writer.json()
    assert data["ok"] is True
    assert data["exists"] is True
    assert data["filename"] == ic.FPV_AUSWEISS_PNG_PATH
    assert data["mime"] == "image/png"
    assert data["size"] == len(content)


@pytest.mark.asyncio
async def test_handle_image_404_when_missing():
    writer = FakeWriter()
    await ic._handle_image(writer)
    assert "404" in writer.status_line
    assert writer.json()["ok"] is False


@pytest.mark.asyncio
async def test_handle_image_streams_existing_file():
    content = b"\xff\xd8\xff\xe0" + b"y" * 2000
    with open(ic.FPV_AUSWEISS_JPG_PATH, "wb") as f:
        f.write(content)
    writer = FakeWriter()
    await ic._handle_image(writer)
    assert "200" in writer.status_line
    assert writer.response.endswith(content)
    assert b"Content-Type: image/jpeg" in writer.response


@pytest.mark.asyncio
async def test_handle_delete_removes_all_variants_and_resets_state():
    open(ic.FPV_AUSWEISS_JPG_PATH, "wb").close()
    open(ic.FPV_AUSWEISS_PNG_PATH, "wb").close()
    writer = FakeWriter()
    await ic._handle_delete(writer)
    assert writer.json() == {"ok": True, "message": "FPV-Ausweis geloescht"}
    assert ic._get_existing_path_and_type() == (None, None)


def _url_decode_noop(value):
    return value


@pytest.mark.asyncio
async def test_upload_chunk_first_chunk_rejects_bad_extension():
    writer = FakeWriter()
    body_text = "index=0&total=2&ext=exe&data="
    await ic._handle_upload_chunk(writer, body_text, {}, _url_decode_noop)
    assert "400" in writer.status_line
    assert writer.json()["ok"] is False


@pytest.mark.asyncio
async def test_upload_chunk_normalizes_jpeg_extension_to_jpg():
    writer = FakeWriter()
    chunk = base64.b64encode(b"\xff\xd8\xff\xe0abc").decode()
    body_text = f"index=0&total=1&ext=jpeg&data={chunk}"
    await ic._handle_upload_chunk(writer, body_text, {}, _url_decode_noop)
    assert "200" in writer.status_line
    assert ic._upload_ext == "jpg"


@pytest.mark.asyncio
async def test_upload_chunk_full_flow_and_finalize():
    content = b"\xff\xd8\xff\xe0" + b"z" * 300
    chunk = base64.b64encode(content).decode()
    writer = FakeWriter()
    body_text = f"index=0&total=1&ext=jpg&data={chunk}"
    await ic._handle_upload_chunk(writer, body_text, {}, _url_decode_noop)
    assert "200" in writer.status_line
    assert ic._upload_received_chunks == 1
    assert ic._upload_total_chunks == 1

    finalize_writer = FakeWriter()
    await ic._handle_finalize(finalize_writer, None)
    assert "200" in finalize_writer.status_line
    result = finalize_writer.json()
    assert result["ok"] is True
    assert result["filename"] == ic.FPV_AUSWEISS_JPG_PATH
    with open(ic.FPV_AUSWEISS_JPG_PATH, "rb") as f:
        assert f.read() == content
    # Upload-Status wird nach erfolgreichem Finalize zurueckgesetzt.
    assert ic._upload_total_chunks == 0


@pytest.mark.asyncio
async def test_finalize_without_upload_fails():
    writer = FakeWriter()
    await ic._handle_finalize(writer, None)
    assert "500" in writer.status_line
    assert writer.json()["ok"] is False


@pytest.mark.asyncio
async def test_finalize_rejects_content_mismatching_extension():
    # Ext behauptet PNG, tatsaechlicher Inhalt ist JPG -> muss abgelehnt werden.
    content = b"\xff\xd8\xff\xe0" + b"z" * 50
    chunk = base64.b64encode(content).decode()
    writer = FakeWriter()
    body_text = f"index=0&total=1&ext=png&data={chunk}"
    await ic._handle_upload_chunk(writer, body_text, {}, _url_decode_noop)

    finalize_writer = FakeWriter()
    await ic._handle_finalize(finalize_writer, None)
    assert "500" in finalize_writer.status_line
    assert "passt nicht" in finalize_writer.json()["error"]


@pytest.mark.asyncio
async def test_finalize_incomplete_upload_fails():
    writer = FakeWriter()
    body_text = "index=0&total=2&ext=jpg&data=" + base64.b64encode(b"\xff\xd8\xff\xe0").decode()
    await ic._handle_upload_chunk(writer, body_text, {}, _url_decode_noop)

    finalize_writer = FakeWriter()
    await ic._handle_finalize(finalize_writer, None)
    assert "500" in finalize_writer.status_line
    assert "unvollstaendig" in finalize_writer.json()["error"]


@pytest.mark.asyncio
async def test_handle_idcard_route_dispatches_known_paths():
    writer = FakeWriter()
    assert await ic.handle_idcard_route(writer, "/idcard-status", "GET", "", {}, _url_decode_noop, None) is True
    assert await ic.handle_idcard_route(writer, "/unknown-route", "GET", "", {}, _url_decode_noop, None) is False


@pytest.mark.asyncio
async def test_handle_idcard_route_upload_chunk_requires_post():
    writer = FakeWriter()
    handled = await ic.handle_idcard_route(writer, "/idcard-upload-chunk", "GET", "", {}, _url_decode_noop, None)
    assert handled is False
