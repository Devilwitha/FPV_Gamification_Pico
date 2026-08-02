import base64
import struct

import pytest

import ota_helpers as ota


def test_url_decode_plus_becomes_space():
    assert ota.url_decode("hello+world") == "hello world"


def test_url_decode_percent_escapes():
    assert ota.url_decode("a%20b%2Fc") == "a b/c"


def test_url_decode_invalid_escape_kept_literal():
    assert ota.url_decode("100%") == "100%"
    assert ota.url_decode("50%zz") == "50%zz"


def test_parse_query_empty_string_returns_empty_dict():
    assert ota.parse_query("") == {}


def test_parse_query_basic_pairs():
    assert ota.parse_query("a=1&b=2") == {"a": "1", "b": "2"}


def test_parse_query_key_without_value():
    assert ota.parse_query("flag&a=1") == {"flag": "", "a": "1"}


def test_parse_query_decodes_percent_and_plus():
    assert ota.parse_query("name=John+Doe&city=New%20York") == {
        "name": "John Doe",
        "city": "New York",
    }


def test_parse_query_ignores_empty_segments():
    assert ota.parse_query("a=1&&b=2&") == {"a": "1", "b": "2"}


def _b64_encode_str(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def test_safe_base64_decode_to_file_roundtrip():
    payload = b"Hello, this is binary-ish test data \x00\x01\x02" * 5
    b64_text = _b64_encode_str(payload)
    ok = ota.safe_base64_decode_to_file(b64_text, "out.bin")
    assert ok is True
    with open("out.bin", "rb") as f:
        assert f.read() == payload


def test_safe_base64_file_to_file_roundtrip_across_chunk_boundaries():
    payload = bytes(range(256)) * 10  # groesser als der interne 512-Byte-Chunk
    b64_text = _b64_encode_str(payload)
    with open("in.b64", "w") as f:
        f.write(b64_text)

    result = ota.safe_base64_file_to_file("in.b64", "out.bin")
    assert result is True
    with open("out.bin", "rb") as f:
        assert f.read() == payload


def test_safe_base64_file_to_file_calls_feed_wdt():
    payload = b"x" * 3000
    with open("in.b64", "w") as f:
        f.write(_b64_encode_str(payload))

    calls = []
    ota.safe_base64_file_to_file("in.b64", "out.bin", feed_wdt=lambda: calls.append(1))
    assert len(calls) > 0


def test_safe_base64_file_to_file_reports_error_on_missing_input():
    logs = []
    result = ota.safe_base64_file_to_file("does_not_exist.b64", "out.bin", log=logs.append)
    assert result != True  # noqa: E712 - Funktion liefert bei Fehler einen String
    assert isinstance(result, str)
    assert logs


def test_read_exact_returns_requested_bytes():
    with open("data.bin", "wb") as f:
        f.write(b"0123456789")
    with open("data.bin", "rb") as f:
        assert ota.read_exact(f, 5) == b"01234"
        assert ota.read_exact(f, 5) == b"56789"


def test_read_exact_returns_less_at_eof():
    with open("data.bin", "wb") as f:
        f.write(b"abc")
    with open("data.bin", "rb") as f:
        assert ota.read_exact(f, 10) == b"abc"


BUNDLE_MAGIC = b"TESTBNDL"


def _build_bundle_bytes(files):
    """files: list of (filename, content_bytes)"""
    out = bytearray()
    out += BUNDLE_MAGIC
    out += struct.pack(">I", len(files))
    for name, content in files:
        name_bytes = name.encode("utf-8")
        out += struct.pack(">I", len(name_bytes))
        out += name_bytes
        out += struct.pack(">I", len(content))
        out += content
    return bytes(out)


def test_apply_firmware_bundle_extracts_files_and_flags_restart():
    bundle = _build_bundle_bytes([
        ("main.py", b"print('hello')\n"),
        ("index.html", b"<html></html>"),
    ])
    with open("firmware.nbo", "wb") as f:
        f.write(bundle)

    extracted, needs_restart = ota.apply_firmware_bundle("firmware.nbo", (), BUNDLE_MAGIC)
    assert set(extracted) == {"main.py", "index.html"}
    assert needs_restart is True
    with open("main.py") as f:
        assert f.read() == "print('hello')\n"


def test_apply_firmware_bundle_no_restart_without_main():
    bundle = _build_bundle_bytes([("index.html", b"<html></html>")])
    with open("firmware.nbo", "wb") as f:
        f.write(bundle)
    _extracted, needs_restart = ota.apply_firmware_bundle("firmware.nbo", (), BUNDLE_MAGIC)
    assert needs_restart is False


def test_apply_firmware_bundle_rejects_bad_magic():
    with open("firmware.nbo", "wb") as f:
        f.write(b"WRONGMAGIC" + struct.pack(">I", 0))
    with pytest.raises(Exception, match="Magic-Header"):
        ota.apply_firmware_bundle("firmware.nbo", (), BUNDLE_MAGIC)


def test_apply_firmware_bundle_rejects_path_traversal_filename():
    bundle = _build_bundle_bytes([("../evil.py", b"pwned")])
    with open("firmware.nbo", "wb") as f:
        f.write(bundle)
    with pytest.raises(Exception, match="nicht erlaubt"):
        ota.apply_firmware_bundle("firmware.nbo", (), BUNDLE_MAGIC)


def test_apply_firmware_bundle_rejects_protected_filenames():
    bundle = _build_bundle_bytes([("license.lic", b"fake-license")])
    with open("firmware.nbo", "wb") as f:
        f.write(bundle)
    with pytest.raises(Exception, match="nicht erlaubt"):
        ota.apply_firmware_bundle("firmware.nbo", (), BUNDLE_MAGIC)


def test_apply_firmware_bundle_removes_stale_py_when_mpy_extracted():
    with open("mymodule.py", "w") as f:
        f.write("old source")
    bundle = _build_bundle_bytes([("mymodule.mpy", b"\x00compiled")])
    with open("firmware.nbo", "wb") as f:
        f.write(bundle)
    ota.apply_firmware_bundle("firmware.nbo", (), BUNDLE_MAGIC)
    import os
    assert not os.path.exists("mymodule.py")
    assert os.path.exists("mymodule.mpy")


def test_apply_firmware_bundle_calls_feed_wdt_per_file():
    bundle = _build_bundle_bytes([("a.py", b"1"), ("b.py", b"2")])
    with open("firmware.nbo", "wb") as f:
        f.write(bundle)
    calls = []
    ota.apply_firmware_bundle("firmware.nbo", (), BUNDLE_MAGIC, feed_wdt=lambda: calls.append(1))
    assert len(calls) >= 2


def test_apply_firmware_bundle_from_base64_matches_binary_variant():
    bundle = _build_bundle_bytes([("main.py", b"print(1)\n"), ("index.html", b"<h1>x</h1>")])
    with open("firmware.b64", "w") as f:
        f.write(_b64_encode_str(bundle))

    extracted, needs_restart = ota.apply_firmware_bundle_from_base64("firmware.b64", (), BUNDLE_MAGIC)
    assert set(extracted) == {"main.py", "index.html"}
    assert needs_restart is True
    with open("main.py") as f:
        assert f.read() == "print(1)\n"
    with open("index.html") as f:
        assert f.read() == "<h1>x</h1>"


def test_base64_file_reader_matches_full_decode_for_various_sizes():
    for size in (0, 1, 3, 4, 100, 513, 2048):
        payload = bytes((i % 256 for i in range(size)))
        text = base64.b64encode(payload).decode("ascii")

        import io
        reader = ota.Base64FileReader(io.StringIO(text))
        out = bytearray()
        while True:
            chunk = reader.read(37)
            if not chunk:
                break
            out.extend(chunk)
        assert bytes(out) == payload
