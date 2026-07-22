import os
import re
import sys
from tkinter import filedialog, Tk

def choose_and_analyze_file(file_path=None):
    if file_path:
        candidate = os.path.abspath(file_path)
        if not os.path.isfile(candidate):
            print(f"Fehler: Datei nicht gefunden: {candidate}")
            return
        analyze_file_for_ota(candidate)
        return

    # Unsichtbares Tkinter-Hauptfenster initialisieren
    root = Tk()
    root.withdraw()
    root.attributes("-topmost", True)  # Fenster in den Vordergrund bringen

    # Ermittle das Verzeichnis, in dem dieses Skript ausgeführt wird
    current_dir = os.path.dirname(os.path.abspath(__file__))

    print(f"Öffne Dateimanager im Verzeichnis: {current_dir}")
    print("Bitte wähle die zu prüfende Python-Datei aus.")
    
    # Dateiwähler öffnen und im aktuellen Skript-Ordner starten
    file_path = filedialog.askopenfilename(
        initialdir=current_dir,
        title="Python-Datei für OTA-Prüfung auswählen",
        filetypes=[("Python-Dateien", "*.py"), ("Alle Dateien", "*.*")]
    )
    
    # Falls der Benutzer den Dialog abbricht
    if not file_path:
        print("Abgebrochen: Keine Datei ausgewählt.")
        return

    # Analyse starten
    analyze_file_for_ota(file_path)


def analyze_file_for_ota(file_path):
    print("=" * 60)
    print(f" Starte OTA-Eignungsprüfung für: {os.path.basename(file_path)}")
    print(f" Pfad: {file_path}")
    print("=" * 60)

    # Typische Mojibake-Muster (mehrfache UTF-8 / ANSI Fehldekodierungen)
    mojibake_pattern = re.compile(r'(ÃƒÆ’|Ã†â€™|Ã¢â‚¬|Â¼|Â°|Ã‚Â|Ã¢â€šÂ¬|Ã¢â€žÂ¢)')
    
    warnings = 0
    critical_errors = 0
    total_lines = 0
    
    mojibake_findings = []
    non_ascii_findings = []

    try:
        # Datei binär lesen, um Encoding-Fehler beim Einlesen selbst zu vermeiden
        with open(file_path, 'rb') as f:
            content_bytes = f.read()
            
        content = content_bytes.decode('utf-8', errors='replace')
        lines = content.splitlines()
        total_lines = len(lines)

        # 1. Analyse der einzelnen Zeilen
        for line_num, line in enumerate(lines, 1):
            if mojibake_pattern.search(line):
                mojibake_findings.append((line_num, line.strip()))
                critical_errors += 1
            else:
                for char in line:
                    if ord(char) > 127 and char != '':
                        non_ascii_findings.append((line_num, char, line.strip()))
                        warnings += 1

        # 2. Analyse der Dateigröße (RAM-Gefahr für den Pico)
        file_size_kb = len(content_bytes) / 1024
        print(f"[*] Dateigröße: {file_size_kb:.2f} KB ({total_lines} Zeilen)")

        if file_size_kb > 45:
            print("[CRITICAL] Datei ist sehr groß für den Pico RAM beim Base64-Decodieren!")
            critical_errors += 1
        elif file_size_kb > 25:
            print("[WARNUNG] Datei ist relativ groß. Überwachung des RAM (gc.collect()) dringend nötig.")
            warnings += 1

        print("-" * 60)

        # 3. Auswertung der Funde
        if mojibake_findings:
            print(f"[X] KRITISCH: {len(mojibake_findings)} Zeilen mit kryptischem Zeichensalat gefunden:")
            for num, text in mojibake_findings[:5]:
                print(f"    Zeile {num}: {text[:80]}")
            if len(mojibake_findings) > 5:
                print(f"    ... und {len(mojibake_findings) - 5} weitere Zeilen.")
            print("")

        if non_ascii_findings:
            print(f"[!] WARNUNG: {len(non_ascii_findings)} Nicht-ASCII-Zeichen (z.B. Umlaute) gefunden:")
            shown_lines = set()
            for num, char, text in non_ascii_findings:
                if num not in shown_lines and len(shown_lines) < 5:
                    print(f"    Zeile {num} (Zeichen '{char}'): {text[:80]}")
                    shown_lines.add(num)
            if len(shown_lines) > 5:
                print(f"    ... und weitere Zeilen mit Sonderzeichen.")
            print("    Hinweis: Umlaute in Strings/Kommentaren sind riskant, wenn das OTA-System kein UTF-8 erzwingt.")
            print("")

        # 4. Endgültiges Fazit
        print("=" * 60)
        print(" ERGEBNIS DER PRÜFUNG:")
        print("=" * 60)
        print(f" Kritische Fehler: {critical_errors}")
        print(f" Warnungen:        {warnings}")
        print("-" * 60)

        if critical_errors > 0:
            print("[FAIL] NICHT FUER OTA GEEIGNET!")
            print(" Grund: Die Datei enthält schweren Zeichensalat oder ist zu groß.")
            print(" Risiko: Der Pico wird nach dem OTA-Update sehr wahrscheinlich einfrieren oder im Bootloop landen.")
        elif warnings > 0:
            print("[WARN] BESCHRAENKT FUER OTA GEEIGNET (Risiko vorhanden)")
            print(" Grund: Keine kritischen Syntax-Fehler, aber ungesicherte Sonderzeichen/Umlaute.")
            print(" Empfehlung: Ersetze Umlaute durch ae, oe, ue, um Zeichenfehler bei der Übertragung zu vermeiden.")
        else:
            print("[OK] PERFEKT FUER OTA GEEIGNET!")
            print(" Die Datei ist absolut sauber (reines ASCII) und hat eine ideale Größe für den Pico.")

    except Exception as e:
        print(f"Ein unerwarteter Fehler ist bei der Analyse aufgetreten: {e}")

if __name__ == "__main__":
    arg_path = sys.argv[1] if len(sys.argv) > 1 else None
    choose_and_analyze_file(arg_path)