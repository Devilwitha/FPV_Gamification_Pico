"""
build_firmware.py

Verpackt alle Firmware-Dateien (main.py + alle Admin-/HTML-Seiten) des
FPV_Gamification_Pico Projekts in eine einzelne Bundle-Datei "firmware.nbo".

Diese Datei kann anschliessend ueber den Admin-Bereich (/admin-update)
per OTA-Update in EINEM Rutsch auf den Pico hochgeladen werden - der
Pico entpackt das Bundle serverseitig und ersetzt alle enthaltenen
Dateien (main.py, index.html, admin_*.html) automatisch.

Nutzung (auf dem PC, mit normalem Python 3, NICHT auf dem Pico ausfuehren):
    python build_firmware.py [output_path]

Ohne Argument wird "firmware.nbo" im aktuellen Verzeichnis erzeugt.

Bundle-Format (einfach, ohne Abhaengigkeiten wie zipfile/tarfile, damit
main.py es mit reinem MicroPython + struct wieder einlesen kann):

    Offset  Groesse   Inhalt
    0       8 Bytes   Magic-Header b"FPVBNDL1"
    8       4 Bytes   Anzahl Dateien (big-endian uint32)
    ...     pro Datei:
              4 Bytes   Laenge des Dateinamens (big-endian uint32)
              N Bytes   Dateiname (UTF-8)
              4 Bytes   Laenge des Dateiinhalts (big-endian uint32)
              M Bytes   Dateiinhalt (roh, binaer)
"""
import os
import struct
import sys

BUNDLE_MAGIC = b"FPVBNDL1"

# Dateien, die im Bundle enthalten sein sollen. Muss mit OTA_ALLOWED_TARGETS
# in main.py uebereinstimmen (dort steht die serverseitige Whitelist).
FILES_TO_BUNDLE = [
    "main.py",
    "index.html",
    "admin_dashboard.html",
    "admin_update.html",
    "admin_simulate.html",
    "admin_profiles.html",
    "admin_system.html",
]


def build_bundle(source_dir, output_path):
    included = []
    missing = []

    for filename in FILES_TO_BUNDLE:
        file_path = os.path.join(source_dir, filename)
        if not os.path.isfile(file_path):
            missing.append(filename)

    if missing:
        print("WARNUNG: Folgende Dateien fehlen und werden NICHT ins Bundle aufgenommen:")
        for name in missing:
            print(f"  - {name}")
        print()

    with open(output_path, "wb") as out:
        out.write(BUNDLE_MAGIC)

        files_present = [f for f in FILES_TO_BUNDLE if f not in missing]
        out.write(struct.pack(">I", len(files_present)))

        for filename in files_present:
            file_path = os.path.join(source_dir, filename)
            with open(file_path, "rb") as f:
                content = f.read()

            name_bytes = filename.encode("utf-8")
            out.write(struct.pack(">I", len(name_bytes)))
            out.write(name_bytes)
            out.write(struct.pack(">I", len(content)))
            out.write(content)

            included.append((filename, len(content)))

    return included, missing


def main():
    source_dir = os.path.dirname(os.path.abspath(__file__))
    output_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(source_dir, "firmware.nbo")

    included, missing = build_bundle(source_dir, output_path)

    total_size = sum(size for _, size in included)
    bundle_size = os.path.getsize(output_path)

    print(f"Firmware-Bundle erstellt: {output_path}")
    print()
    print(f"{'Datei':<28} {'Groesse':>10}")
    print("-" * 40)
    for filename, size in included:
        print(f"{filename:<28} {size:>8} B")
    print("-" * 40)
    print(f"{'Summe (Inhalte)':<28} {total_size:>8} B")
    print(f"{'Bundle-Datei gesamt':<28} {bundle_size:>8} B")

    if missing:
        print()
        print(f"HINWEIS: {len(missing)} Datei(en) fehlten und wurden uebersprungen (siehe oben).")

    print()
    print("Naechster Schritt: firmware.nbo im Admin-Bereich unter /admin-update hochladen.")


if __name__ == "__main__":
    main()
