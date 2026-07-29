"""license_generator.py - PC-seitige RSA-Schluesselerzeugung und Signierung
von license.lic-Dateien fuer FPV_Gamification_Pico.

Das Lizenzformat ist bewusst kein JSON (keine Kanonisierungs-Unschaerfen beim
winzigen On-Device-Parser in source/license_verifier.py), sondern ein festes
Klartext-Format:

    hardware_id=<hex>
    customer_id=<Text>
    issued=<YYYY-MM-DD>
    ---SIGNATURE---
    <Base64 RSA-PKCS#1v1.5-SHA256-Signatur ueber genau die drei Zeilen oben>

Es gibt (bewusst, siehe Spezifikation) kein Ablaufdatum: der Pico hat keine
batteriegepufferte RTC und synct nirgends per NTP, ein Expiry-Feld waere
ohne vertrauenswuerdige Uhrzeit rein kosmetisch. Lizenzen sind unbefristet.

Benoetigt das Paket 'cryptography' (siehe requirements/requirements.txt).
"""
import datetime
import os

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization

SIGNATURE_MARKER = "---SIGNATURE---"
DEFAULT_KEY_SIZE = 2048
DEFAULT_PUBLIC_EXPONENT = 65537


def generate_keypair(private_key_path, public_key_path, key_size=DEFAULT_KEY_SIZE):
    """Erzeugt ein neues RSA-Schluesselpaar und schreibt beide Dateien.

    private_key_path bleibt ausschliesslich auf dem PC (nie auf den Pico
    uebertragen); public_key_path wird spaeter mit auf den Pico geschrieben
    und von source/license_verifier.py geladen."""
    private_key = rsa.generate_private_key(
        public_exponent=DEFAULT_PUBLIC_EXPONENT,
        key_size=key_size,
    )

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(private_key_path, "wb") as f:
        f.write(private_bytes)

    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with open(public_key_path, "wb") as f:
        f.write(public_bytes)

    return private_key


def load_private_key(private_key_path):
    with open(private_key_path, "rb") as f:
        return serialization.load_pem_private_key(f.read(), password=None)


def build_license_payload(hardware_id, customer_id, issued=None):
    hardware_id = hardware_id.strip().lower()
    customer_id = customer_id.strip().replace("\n", " ").replace("\r", " ")
    if issued is None:
        issued = datetime.date.today().isoformat()

    if not hardware_id:
        raise ValueError("hardware_id darf nicht leer sein")

    return (
        f"hardware_id={hardware_id}\n"
        f"customer_id={customer_id}\n"
        f"issued={issued}\n"
    )


def sign_license(private_key, hardware_id, customer_id, issued=None):
    """Erzeugt den vollstaendigen Inhalt einer license.lic-Datei."""
    payload = build_license_payload(hardware_id, customer_id, issued=issued)
    signature = private_key.sign(
        payload.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    signature_b64 = serialization_b64encode(signature)
    return payload + SIGNATURE_MARKER + "\n" + signature_b64 + "\n"


def serialization_b64encode(data):
    import base64
    return base64.b64encode(data).decode("ascii")


def sign_license_from_key_file(private_key_path, hardware_id, customer_id, issued=None):
    private_key = load_private_key(private_key_path)
    return sign_license(private_key, hardware_id, customer_id, issued=issued)


def save_license(content, license_path):
    with open(license_path, "w", newline="\n") as f:
        f.write(content)


def _self_test_verify(license_content, public_key_path, expected_hardware_id):
    """Optionaler Rundlauf-Selbsttest: nutzt DIESELBE Verify-Logik wie der
    Pico (source/license_verifier.py), damit ein Formatfehler sofort beim
    Erzeugen auffaellt statt erst beim Testen auf echter Hardware."""
    import sys
    source_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "source")
    if source_dir not in sys.path:
        sys.path.insert(0, source_dir)
    import license_verifier

    parsed = license_verifier.parse_license_text(license_content)
    if parsed is None:
        raise AssertionError("Selbsttest fehlgeschlagen: license.lic-Format ungueltig")

    n, e = license_verifier.load_public_key(public_key_path)
    import binascii
    signature = binascii.a2b_base64(parsed["signature_b64"])
    if not license_verifier._rsa_verify_pkcs1_sha256(parsed["signed_payload"], signature, n, e):
        raise AssertionError("Selbsttest fehlgeschlagen: Signatur passt nicht zum Public Key")

    if parsed["fields"].get("hardware_id", "").lower() != expected_hardware_id.strip().lower():
        raise AssertionError("Selbsttest fehlgeschlagen: hardware_id stimmt nicht ueberein")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RSA-Schluessel erzeugen oder license.lic signieren")
    subparsers = parser.add_subparsers(dest="command", required=True)

    gen_parser = subparsers.add_parser("generate-keys", help="Neues RSA-Schluesselpaar erzeugen")
    gen_parser.add_argument("--private-key", default="private_key.pem")
    gen_parser.add_argument("--public-key", default="public_key.pem")
    gen_parser.add_argument("--key-size", type=int, default=DEFAULT_KEY_SIZE)

    sign_parser = subparsers.add_parser("sign", help="license.lic fuer eine Hardware-ID signieren")
    sign_parser.add_argument("--private-key", default="private_key.pem")
    sign_parser.add_argument("--public-key", default="public_key.pem")
    sign_parser.add_argument("--hardware-id", required=True)
    sign_parser.add_argument("--customer-id", default="")
    sign_parser.add_argument("--out", default="license.lic")

    args = parser.parse_args()

    if args.command == "generate-keys":
        generate_keypair(args.private_key, args.public_key, key_size=args.key_size)
        print(f"Schluesselpaar erzeugt: {args.private_key} (PRIVAT, geheim halten!) / {args.public_key}")
    elif args.command == "sign":
        content = sign_license_from_key_file(args.private_key, args.hardware_id, args.customer_id)
        save_license(content, args.out)
        _self_test_verify(content, args.public_key, args.hardware_id)
        print(f"license.lic erzeugt und selbst verifiziert: {args.out}")
