"""license_verifier.py - prueft /license.lic gegen /public_key.pem und die
Hardware-ID (machine.unique_id()) dieses Pico.

Reine Python-Implementierung (kein externes Krypto-Modul - MicroPython hat
keins fuer RSA): RSA-Modexp per eingebautem pow(base, exp, mod), SHA-256 per
hashlib (in MicroPython vorhanden), PKCS#1 v1.5-Padding-Pruefung von Hand.
Der Public Key ist ein normales X.509 SubjectPublicKeyInfo PEM (mit openssl
inspizierbar) - dafuer parst dieses Modul nur genau die dafuer noetige,
minimale ASN.1/DER-Struktur (keine generische ASN.1-Bibliothek).

Laeuft bewusst auch unter CPython (fuer PC-seitige Tests von
tools/license_generator.py) - 'machine' wird nur in get_pico_id() lazy
importiert, alles andere ist reines Python + hashlib + binascii.
"""
import binascii

# Bekannter ASN.1-DigestInfo-Praefix fuer SHA-256 (RFC 8017 Anhang), gefolgt
# vom 32-Byte-Hash selbst. Siehe z.B. die "rsa" PyPI-Bibliothek fuer denselben
# Konstantenwert.
_SHA256_DIGESTINFO_PREFIX = b'\x30\x31\x30\x0d\x06\x09\x60\x86\x48\x01\x65\x03\x04\x02\x01\x05\x00\x04\x20'

SIGNATURE_MARKER = "---SIGNATURE---"


def get_pico_id():
    import machine
    return binascii.hexlify(machine.unique_id()).decode('utf-8')


def _sha256(data):
    import hashlib
    h = hashlib.sha256()
    h.update(data)
    return h.digest()


def _der_read_length(data, idx):
    first = data[idx]
    idx += 1
    if first & 0x80 == 0:
        return first, idx
    num_bytes = first & 0x7f
    length = 0
    for _ in range(num_bytes):
        length = (length << 8) | data[idx]
        idx += 1
    return length, idx


def _der_read_tlv(data, idx):
    tag = data[idx]
    idx += 1
    length, idx = _der_read_length(data, idx)
    value = data[idx:idx + length]
    idx += length
    return tag, value, idx


def _parse_rsa_public_key_der(der):
    """Parst ein X.509 SubjectPublicKeyInfo (RSA) DER-Blob und liefert (n, e)."""
    _tag, spki, _idx = _der_read_tlv(der, 0)

    idx = 0
    _tag, _alg_id, idx = _der_read_tlv(spki, idx)      # AlgorithmIdentifier (rsaEncryption) - ignoriert
    _tag, bit_string, idx = _der_read_tlv(spki, idx)   # BIT STRING mit dem eingebetteten RSAPublicKey

    # Erstes Byte der BIT STRING ist die Anzahl ungenutzter Bits (muss 0 sein).
    rsa_pub_der = bit_string[1:]
    _tag, rsa_seq, _idx2 = _der_read_tlv(rsa_pub_der, 0)

    idx3 = 0
    _tag, modulus_bytes, idx3 = _der_read_tlv(rsa_seq, idx3)
    _tag, exponent_bytes, idx3 = _der_read_tlv(rsa_seq, idx3)

    n = int.from_bytes(modulus_bytes, 'big')
    e = int.from_bytes(exponent_bytes, 'big')
    return n, e


def load_public_key(path="public_key.pem"):
    with open(path, "r") as f:
        pem = f.read()
    b64_lines = [
        line.strip() for line in pem.strip().split('\n')
        if line.strip() and not line.strip().startswith('-----')
    ]
    der = binascii.a2b_base64(''.join(b64_lines))
    return _parse_rsa_public_key_der(der)


def _bit_length(x):
    """Portabler Ersatz fuer int.bit_length(): auf manchen MicroPython-
    Builds (z.B. RP2350/1.28.0, per echtem Hardwaretest bestaetigt) fehlt
    diese Methode auf grossen (mpz-)Ints, obwohl kleine Ints sie haben."""
    bits = 0
    while x:
        x >>= 1
        bits += 1
    return bits


def _rsa_verify_pkcs1_sha256(message, signature, n, e):
    k = (_bit_length(n) + 7) // 8
    if len(signature) != k:
        return False

    s = int.from_bytes(signature, 'big')
    if s >= n:
        return False

    m = pow(s, e, n)
    em = m.to_bytes(k, 'big')

    if em[0] != 0x00 or em[1] != 0x01:
        return False

    idx = 2
    while idx < k and em[idx] == 0xFF:
        idx += 1
    if (idx - 2) < 8 or idx >= k or em[idx] != 0x00:
        return False
    idx += 1

    expected = _SHA256_DIGESTINFO_PREFIX + _sha256(message)
    return em[idx:] == expected


def parse_license_text(text):
    """Zerlegt den Inhalt einer license.lic in Felder + signierte Nutzlast +
    Base64-Signatur. Liefert None bei fehlerhaftem Format."""
    marker_idx = text.find(SIGNATURE_MARKER)
    if marker_idx < 0:
        return None

    signed_part = text[:marker_idx]
    sig_b64 = text[marker_idx + len(SIGNATURE_MARKER):].strip()
    if not sig_b64:
        return None

    fields = {}
    for line in signed_part.split('\n'):
        line = line.strip()
        if not line or '=' not in line:
            continue
        key, _, value = line.partition('=')
        fields[key.strip()] = value.strip()

    if "hardware_id" not in fields:
        return None

    return {
        "fields": fields,
        "signed_payload": signed_part.encode('utf-8'),
        "signature_b64": sig_b64,
    }


def verify(license_path="license.lic", public_key_path="public_key.pem", expected_hardware_id=None):
    """Liefert 'VALID', 'INVALID' oder 'MISSING'."""
    try:
        with open(license_path, "r") as f:
            text = f.read()
    except Exception:
        return "MISSING"

    parsed = parse_license_text(text)
    if parsed is None:
        return "INVALID"

    try:
        n, e = load_public_key(public_key_path)
    except Exception:
        return "INVALID"

    try:
        signature = binascii.a2b_base64(parsed["signature_b64"])
    except Exception:
        return "INVALID"

    try:
        if not _rsa_verify_pkcs1_sha256(parsed["signed_payload"], signature, n, e):
            return "INVALID"
    except Exception:
        return "INVALID"

    if expected_hardware_id is None:
        try:
            expected_hardware_id = get_pico_id()
        except Exception:
            return "INVALID"

    hardware_id = parsed["fields"].get("hardware_id", "")
    if hardware_id.lower() != expected_hardware_id.lower():
        return "INVALID"

    return "VALID"
