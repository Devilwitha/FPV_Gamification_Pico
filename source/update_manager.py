"""update_manager.py - zentrale Liste der vor Updates geschuetzten Dateien.

license.lic (Lizenzdatei) und public_key.pem (vertrauenswuerdiger RSA-Public-
Key fuer license_verifier.py) duerfen NIE durch ein Firmware-Update (weder
firmware.nbo/lang.pak-Bundle noch GitHub-OTA) geloescht oder ueberschrieben
werden - sonst wuerde ein normales Update eine bestehende Lizenz zerstoeren
bzw. koennte ein manipuliertes Update den Public Key austauschen und damit
beliebige Lizenzen faelschbar machen. Wird von ota_helpers.py's
_is_safe_bundle_entry_filename() genutzt, um solche Dateinamen aus jedem
Bundle-Eintrag abzulehnen.
"""

PROTECTED_FILES = frozenset({"license.lic", "public_key.pem"})


def is_protected(filename):
    return filename in PROTECTED_FILES
