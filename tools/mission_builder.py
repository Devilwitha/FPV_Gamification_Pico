"""
mission_builder.py - Editor + Uploader fuer Challenge-"Missionen" (.mission Dateien).

Analog zu profilemanager.py (lokaler Editor fuer Trick-Tuning-Profile) und
build_firmware.py (Bundle-Bau + Upload per Web/Seriell), aber fuer die
Real-Time Challenges (Touch & Go, Altitude-Hold/Limbo, Eco-Challenge) aus
source/challenge_helpers.py.

Eine Mission ist eine kleine JSON-Datei mit der Endung ".mission":

    {
      "name": "Sonntagslimbo",
      "challenge_type": "altitude_hold",   # touch_and_go | altitude_hold | eco
      "description": "Kurzbeschreibung fuer die Anzeige im Web",
      "params": { "mode": "limbo", "ceiling_m": 0.8, "duration_s": 25 }
    }

Missionen werden lokal im Unterordner "missionen" (neben diesem Skript)
bearbeitet/gespeichert/gesucht und koennen von hier aus direkt auf den Pico
hochgeladen werden:

- Per Web (HTTP): POST an <Pico-URL>/mission-upload (wie /create-profile
  bei den Trick-Profilen) - dieselbe kleine Hilfsfunktion _post_form_json()
  wie in build_firmware.py wird wiederverwendet.
- Per USB-Seriell: mpremote kopiert die Datei direkt in den Pico-Dateisystem-
  Root (dieselbe Logik/Fehlerbehandlung wie build_firmware.py's Serial-Upload,
  hier wiederverwendet statt neu implementiert).

Im Gegensatz zur (bewusst einfachen) Missions-Verwaltung auf der Admin-Web-
Seite (/admin-challenges: nur anwenden/hoch-/runterladen/loeschen bereits
fertiger Missionen) erlaubt dieser Editor die volle Detail-Bearbeitung aller
Parameter je Challenge-Typ.
"""
import json
import os
import sys
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# Serial-/Web-Upload-Hilfsfunktionen aus build_firmware.py wiederverwenden,
# statt dieselbe mpremote-/urllib-Logik ein zweites Mal zu pflegen. Der Import
# ist nebenwirkungsfrei (build_firmware.py fuehrt main() nur unter
# "if __name__ == '__main__'" aus).
import build_firmware as fw

APP_TITLE = "Mission Builder"
MISSION_EXTENSION = ".mission"
# Lokale Missionen werden nicht im Projekt-Root, sondern in einem eigenen
# Unterordner "missionen" gespeichert und gesucht - wird beim Start
# automatisch angelegt, falls er fehlt. Dieses Skript liegt im
# tools/-Unterordner, missionen/ liegt aber im Projekt-Root (Elternordner
# von tools/), daher .parent.parent statt nur .parent.
MISSIONS_DIR = Path(__file__).resolve().parent.parent / "missionen"
MISSIONS_DIR.mkdir(parents=True, exist_ok=True)

CHALLENGE_TYPE_LABELS = {
    "touch_and_go": "Touch & Go / Praezisions-Landung",
    "altitude_hold": "Altitude-Hold / Limbo",
    "eco": "Eco-Challenge (Energy Management)",
    "heading_hold": "Heading-Hold / Kurs halten",
    "link_quality": "Signal-Helden (Link-Qualitaet)",
    "speed_run": "Speed-Run / Tempo-Rennen (GPS)",
    "trick": "Trick-Challenge (bestimmten Trick fliegen)",
}

# Direktions-unabhaengige Namen aller Tricks, die main.py's
# LiveGyroTrickDetector.evaluate_trick() bereits standardmaessig erkennt
# (muss exakt zu challenge_helpers.py's TRICK_NAMES passen).
TRICK_NAMES = [
    "Any",
    "Barrel Roll",
    "Double Roll",
    "Super Multi-Roll",
    "Juicy Roll Flick",
    "Power Flip",
    "Split-S / Half-Loop",
    "Double Flip",
    "Super Multi-Flip",
    "Juicy Pitch Flick",
    "Matty Flip Combo",
    "Flat Spin 360",
    "Flat Spin 720",
]

# (key, label, kind, default, choices)
PARAM_SCHEMA = {
    "touch_and_go": [
        ("soft_gyro_max", "Weicher Gyro-Grenzwert (deg/s, volle Punktzahl darunter)", "float", 90.0, None),
        ("hard_gyro_max", "Harter Gyro-Grenzwert (deg/s, 0 Punkte darueber)", "float", 220.0, None),
        ("base_points", "Basis-Punkte bei weicher Landung", "int", 200, None),
    ],
    "altitude_hold": [
        ("mode", "Modus", "choice", "hold", ["hold", "limbo"]),
        ("tolerance_m", "Toleranz um Starthoehe (m, Modus hold)", "float", 0.5, None),
        ("ceiling_m", "Limbo-Decke (m, Modus limbo)", "float", 1.0, None),
        ("duration_s", "Zu haltende Dauer (s)", "float", 20.0, None),
    ],
    "eco": [
        ("points_base", "Basis-Punkte", "float", 500.0, None),
        ("points_per_mah", "Punkte-Abzug pro verbrauchtem mAh", "float", 1.0, None),
    ],
    "heading_hold": [
        ("tolerance_deg", "Toleranz um Start-Kurs (Grad)", "float", 10.0, None),
        ("duration_s", "Zu haltende Dauer (s)", "float", 15.0, None),
    ],
    "link_quality": [
        ("min_lq", "Mindest-Linkqualitaet (%)", "int", 70, None),
        ("max_lq", "Maximal-Linkqualitaet (%, 0=kein Limit)", "int", 0, None),
        ("duration_s", "Zu haltende Dauer (s)", "float", 30.0, None),
    ],
    "speed_run": [
        ("duration_s", "Sprint-Dauer (s)", "float", 10.0, None),
        ("points_per_kmh", "Punkte pro km/h Spitzengeschwindigkeit", "float", 5.0, None),
    ],
    "trick": [
        ("target_name", "Ziel-Trick", "choice", "Any", TRICK_NAMES),
        ("time_limit_s", "Zeitfenster fuer den Trick (s)", "float", 30.0, None),
        ("bonus_points", "Bonus-Punkte bei Erfolg", "int", 50, None),
    ],
}

CHALLENGE_TYPES = list(PARAM_SCHEMA.keys())


def sanitize_mission_name(name):
    """Gleiche Sanitizing-Regel wie source/challenge_helpers.py's
    _sanitize_mission_name(), damit lokale und hochgeladene Dateinamen
    konsistent bleiben (keine Pfad-Traversal, keine Sonderzeichen)."""
    name = str(name or "").strip()
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_", " "))
    cleaned = cleaned.strip().replace(" ", "_")
    return cleaned[:40]


def default_mission_for_type(challenge_type):
    schema = PARAM_SCHEMA.get(challenge_type, [])
    params = {key: default for key, _label, _kind, default, _choices in schema}
    return {
        "name": "Neue Mission",
        "challenge_type": challenge_type,
        "description": "",
        "params": params,
    }


def validate_mission(mission):
    if not isinstance(mission, dict):
        return False, "Mission muss ein JSON-Objekt sein"
    if not str(mission.get("name", "")).strip():
        return False, "Name fehlt"
    challenge_type = mission.get("challenge_type")
    if challenge_type not in PARAM_SCHEMA:
        return False, "Unbekannter challenge_type: %r (erlaubt: %s)" % (challenge_type, ", ".join(CHALLENGE_TYPES))
    if not isinstance(mission.get("params"), dict):
        return False, "params fehlt oder ist kein Objekt"
    return True, ""


def _cast_param_value(kind, raw):
    if kind == "float":
        return float(str(raw).strip().replace(",", "."))
    if kind == "int":
        return int(float(str(raw).strip().replace(",", ".")))
    return str(raw).strip()


class MissionBuilderApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("980x700")
        self.minsize(860, 600)

        self.current_path: "Path | None" = None
        self.param_vars = {}
        self.file_list = {}

        self._build_ui()
        self._refresh_file_list()
        self._new_mission(initial=True)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        # --- Sidebar: lokale .mission Dateien ---
        sidebar = ttk.Frame(self, padding=8)
        sidebar.grid(row=0, column=0, sticky="ns")
        ttk.Label(sidebar, text="Lokale Missionen", font=("", 10, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(sidebar, width=32, height=24, exportselection=False)
        self.listbox.pack(fill="y", expand=False, pady=(4, 6))
        self.listbox.bind("<<ListboxSelect>>", self._on_select_local)

        btns = ttk.Frame(sidebar)
        btns.pack(fill="x")
        ttk.Button(btns, text="Neu", command=self._new_mission).grid(row=0, column=0, sticky="ew", pady=2, padx=1)
        ttk.Button(btns, text="Oeffnen...", command=self._open_dialog).grid(row=0, column=1, sticky="ew", pady=2, padx=1)
        ttk.Button(btns, text="Speichern", command=self._save).grid(row=1, column=0, sticky="ew", pady=2, padx=1)
        ttk.Button(btns, text="Speichern unter...", command=self._save_as).grid(row=1, column=1, sticky="ew", pady=2, padx=1)
        ttk.Button(btns, text="Loeschen", command=self._delete_local).grid(row=2, column=0, sticky="ew", pady=2, padx=1)
        ttk.Button(btns, text="Aktualisieren", command=self._refresh_file_list).grid(row=2, column=1, sticky="ew", pady=2, padx=1)
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)

        # --- Hauptbereich: Editor ---
        main = ttk.Frame(self, padding=10)
        main.grid(row=0, column=1, sticky="nsew")
        main.columnconfigure(1, weight=1)

        row = 0
        ttk.Label(main, text="Name").grid(row=row, column=0, sticky="w", pady=4)
        self.name_var = tk.StringVar()
        ttk.Entry(main, textvariable=self.name_var).grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        ttk.Label(main, text="Challenge-Typ").grid(row=row, column=0, sticky="w", pady=4)
        self.type_var = tk.StringVar(value=CHALLENGE_TYPES[0])
        type_combo = ttk.Combobox(
            main, textvariable=self.type_var, state="readonly",
            values=[f"{t} - {CHALLENGE_TYPE_LABELS[t]}" for t in CHALLENGE_TYPES],
        )
        type_combo.grid(row=row, column=1, sticky="ew", pady=4)
        type_combo.bind("<<ComboboxSelected>>", lambda e: self._on_type_changed())
        self._type_combo = type_combo

        row += 1
        ttk.Label(main, text="Beschreibung").grid(row=row, column=0, sticky="nw", pady=4)
        self.desc_text = tk.Text(main, height=3, wrap="word")
        self.desc_text.grid(row=row, column=1, sticky="ew", pady=4)

        row += 1
        ttk.Separator(main).grid(row=row, column=0, columnspan=2, sticky="ew", pady=8)

        row += 1
        ttk.Label(main, text="Parameter", font=("", 10, "bold")).grid(row=row, column=0, sticky="w")
        row += 1
        self.params_frame = ttk.Frame(main)
        self.params_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        self.params_frame.columnconfigure(1, weight=1)
        self._params_row = row

        row += 1
        ttk.Separator(main).grid(row=row, column=0, columnspan=2, sticky="ew", pady=10)

        # --- Upload: Web ---
        row += 1
        ttk.Label(main, text="Hochladen per Web (HTTP)", font=("", 10, "bold")).grid(row=row, column=0, sticky="w", columnspan=2)
        row += 1
        web_frame = ttk.Frame(main)
        web_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        web_frame.columnconfigure(1, weight=1)
        ttk.Label(web_frame, text="Pico-URL").grid(row=0, column=0, sticky="w")
        self.pico_url_var = tk.StringVar(value=fw.DEFAULT_PICO_URL)
        ttk.Entry(web_frame, textvariable=self.pico_url_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(web_frame, text="Hochladen (Web)", command=self._upload_web).grid(row=0, column=2, padx=2)
        ttk.Button(web_frame, text="Missionen auf Pico anzeigen", command=self._list_remote_missions).grid(row=0, column=3, padx=2)

        # --- Upload: Seriell ---
        row += 1
        ttk.Label(main, text="Hochladen per USB-Seriell", font=("", 10, "bold")).grid(row=row, column=0, sticky="w", columnspan=2)
        row += 1
        serial_frame = ttk.Frame(main)
        serial_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        serial_frame.columnconfigure(1, weight=1)
        ttk.Label(serial_frame, text="Port").grid(row=0, column=0, sticky="w")
        self.port_var = tk.StringVar()
        self.port_combo = ttk.Combobox(serial_frame, textvariable=self.port_var, state="readonly", values=[])
        self.port_combo.grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(serial_frame, text="Ports suchen", command=self._detect_ports).grid(row=0, column=2, padx=2)
        ttk.Button(serial_frame, text="Hochladen (Seriell)", command=self._upload_serial).grid(row=0, column=3, padx=2)

        # --- Status ---
        row += 1
        self.status_var = tk.StringVar(value="Bereit.")
        self.status_label = ttk.Label(main, textvariable=self.status_var, foreground="#2d6a2d")
        self.status_label.grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))

        self._on_type_changed(keep_values=False)

    def _set_status(self, text, error=False):
        # WICHTIG: Wird oft aus Hintergrund-Threads (Web-/Seriell-Upload)
        # aufgerufen. Tkinter-Widgets/Variablen direkt aus einem Thread zu
        # aendern wird von Tcl/Tk nicht zuverlaessig ins Fenster uebernommen
        # (der Text scheint sich nicht zu aendern - Upload wirkt dadurch wie
        # 'passiert nichts'). Deshalb ueber self.after() in den Tkinter-
        # Hauptthread einreihen, das ist der uebliche, zuverlaessige Weg.
        def apply():
            self.status_var.set(text)
            self.status_label.config(foreground="#c0392b" if error else "#2d6a2d")
        try:
            self.after(0, apply)
        except RuntimeError:
            # Fenster evtl. schon geschlossen - dann gibt es nichts mehr zu aktualisieren.
            pass

    # ------------------------------------------------------- Parameter-UI
    def _current_type_key(self):
        raw = self.type_var.get()
        return raw.split(" - ", 1)[0].strip() if raw else CHALLENGE_TYPES[0]

    def _on_type_changed(self, keep_values=False):
        challenge_type = self._current_type_key()
        existing_values = {}
        if keep_values:
            for key, (var, _kind, _label) in self.param_vars.items():
                existing_values[key] = var.get()

        for child in self.params_frame.winfo_children():
            child.destroy()
        self.param_vars = {}

        schema = PARAM_SCHEMA.get(challenge_type, [])
        for i, (key, label, kind, default, choices) in enumerate(schema):
            ttk.Label(self.params_frame, text=label).grid(row=i, column=0, sticky="w", pady=2)
            var = tk.StringVar(value=str(existing_values.get(key, default)))
            if kind == "choice":
                widget = ttk.Combobox(self.params_frame, textvariable=var, state="readonly", values=choices or [])
            else:
                widget = ttk.Entry(self.params_frame, textvariable=var)
            widget.grid(row=i, column=1, sticky="ew", pady=2)
            self.param_vars[key] = (var, kind, label)

    # ------------------------------------------------------------ Formular
    def _collect_mission_from_form(self):
        challenge_type = self._current_type_key()
        params = {}
        for key, (var, kind, label) in self.param_vars.items():
            try:
                params[key] = _cast_param_value(kind, var.get())
            except Exception:
                raise ValueError(f"Ungueltiger Wert bei '{label}': {var.get()!r}")
        mission = {
            "name": self.name_var.get().strip(),
            "challenge_type": challenge_type,
            "description": self.desc_text.get("1.0", "end").strip(),
            "params": params,
        }
        return mission

    def _load_mission_into_form(self, mission):
        self.name_var.set(str(mission.get("name", "")))
        challenge_type = mission.get("challenge_type", CHALLENGE_TYPES[0])
        if challenge_type not in PARAM_SCHEMA:
            challenge_type = CHALLENGE_TYPES[0]
        self.type_var.set(f"{challenge_type} - {CHALLENGE_TYPE_LABELS[challenge_type]}")
        self.desc_text.delete("1.0", "end")
        self.desc_text.insert("1.0", str(mission.get("description", "")))
        self._on_type_changed(keep_values=False)
        params = mission.get("params", {}) or {}
        for key, (var, _kind, _label) in self.param_vars.items():
            if key in params:
                var.set(str(params[key]))

    # -------------------------------------------------------- Datei-Aktionen
    def _refresh_file_list(self):
        self.file_list = {}
        self.listbox.delete(0, "end")
        try:
            paths = sorted(
                [p for p in MISSIONS_DIR.iterdir() if p.is_file() and p.suffix.lower() == MISSION_EXTENSION],
                key=lambda p: p.name.lower(),
            )
        except Exception:
            paths = []
        for p in paths:
            self.file_list[p.name] = p
            self.listbox.insert("end", p.name)

    def _on_select_local(self, _event=None):
        selection = self.listbox.curselection()
        if not selection:
            return
        name = self.listbox.get(selection[0])
        path = self.file_list.get(name)
        if not path:
            return
        try:
            mission = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Konnte Datei nicht lesen:\n{e}")
            return
        self.current_path = path
        self._load_mission_into_form(mission)
        self._set_status(f"Geladen: {path.name}")

    def _new_mission(self, initial=False):
        self.current_path = None
        self._load_mission_into_form(default_mission_for_type(CHALLENGE_TYPES[0]))
        if not initial:
            self._set_status("Neue Mission (noch nicht gespeichert).")

    def _open_dialog(self):
        filename = filedialog.askopenfilename(
            initialdir=str(MISSIONS_DIR),
            filetypes=[("Mission-Dateien", f"*{MISSION_EXTENSION}"), ("Alle Dateien", "*.*")],
        )
        if not filename:
            return
        path = Path(filename)
        try:
            mission = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Konnte Datei nicht lesen:\n{e}")
            return
        self.current_path = path
        self._load_mission_into_form(mission)
        self._set_status(f"Geladen: {path.name}")

    def _target_path_for_name(self, name):
        safe_name = sanitize_mission_name(name)
        if not safe_name:
            return None
        return MISSIONS_DIR / f"{safe_name}{MISSION_EXTENSION}"

    def _save(self):
        try:
            mission = self._collect_mission_from_form()
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        ok, err = validate_mission(mission)
        if not ok:
            messagebox.showerror(APP_TITLE, err)
            return
        path = self.current_path or self._target_path_for_name(mission["name"])
        if path is None:
            messagebox.showerror(APP_TITLE, "Ungueltiger Name.")
            return
        try:
            path.write_text(json.dumps(mission, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Speichern fehlgeschlagen:\n{e}")
            return
        self.current_path = path
        self._refresh_file_list()
        self._set_status(f"Gespeichert: {path.name}")

    def _save_as(self):
        try:
            mission = self._collect_mission_from_form()
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        ok, err = validate_mission(mission)
        if not ok:
            messagebox.showerror(APP_TITLE, err)
            return
        filename = filedialog.asksaveasfilename(
            initialdir=str(MISSIONS_DIR),
            defaultextension=MISSION_EXTENSION,
            filetypes=[("Mission-Dateien", f"*{MISSION_EXTENSION}"), ("Alle Dateien", "*.*")],
            initialfile=sanitize_mission_name(mission["name"]) + MISSION_EXTENSION,
        )
        if not filename:
            return
        path = Path(filename)
        if path.suffix.lower() != MISSION_EXTENSION:
            path = path.with_suffix(MISSION_EXTENSION)
        try:
            path.write_text(json.dumps(mission, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Speichern fehlgeschlagen:\n{e}")
            return
        self.current_path = path
        self._refresh_file_list()
        self._set_status(f"Gespeichert: {path.name}")

    def _delete_local(self):
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showinfo(APP_TITLE, "Bitte zuerst eine Datei in der Liste auswaehlen.")
            return
        name = self.listbox.get(selection[0])
        path = self.file_list.get(name)
        if not path:
            return
        if not messagebox.askyesno(APP_TITLE, f"'{name}' wirklich loeschen?"):
            return
        try:
            path.unlink()
        except Exception as e:
            messagebox.showerror(APP_TITLE, f"Loeschen fehlgeschlagen:\n{e}")
            return
        if self.current_path == path:
            self.current_path = None
        self._refresh_file_list()
        self._set_status(f"Geloescht: {name}")

    # ---------------------------------------------------------------- Upload
    def _upload_web(self):
        try:
            mission = self._collect_mission_from_form()
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        ok, err = validate_mission(mission)
        if not ok:
            messagebox.showerror(APP_TITLE, err)
            return
        base_url = fw.normalize_base_url(self.pico_url_var.get())

        def worker():
            self._set_status("Hochladen per Web laeuft...")
            try:
                result = fw._post_form_json(
                    base_url + "/mission-upload",
                    {"name": mission["name"], "data": json.dumps(mission)},
                    timeout=10,
                )
                if result.get("ok"):
                    self._set_status(f"Web-Upload OK: {mission['name']} -> {base_url}")
                else:
                    self._set_status(f"Web-Upload Fehler: {result.get('error')}", error=True)
            except Exception as e:
                self._set_status(f"Web-Upload fehlgeschlagen: {e}", error=True)

        threading.Thread(target=worker, daemon=True).start()

    def _list_remote_missions(self):
        base_url = fw.normalize_base_url(self.pico_url_var.get())

        def worker():
            self._set_status("Lade Missionsliste vom Pico...")
            try:
                result = fw._get_json(base_url + "/missions-list", timeout=10)
                missions = result.get("missions", []) if result.get("ok") else []
            except Exception as e:
                self._set_status(f"Abruf fehlgeschlagen: {e}", error=True)
                return
            self.after(0, lambda: self._show_remote_missions_dialog(base_url, missions))
            self._set_status(f"{len(missions)} Mission(en) auf dem Pico gefunden.")

        threading.Thread(target=worker, daemon=True).start()

    def _show_remote_missions_dialog(self, base_url, missions):
        dialog = tk.Toplevel(self)
        dialog.title("Missionen auf dem Pico")
        dialog.geometry("480x320")
        lb = tk.Listbox(dialog, width=60)
        lb.pack(fill="both", expand=True, padx=8, pady=8)
        for m in missions:
            lb.insert("end", f"{m.get('name')} [{m.get('challenge_type')}] - {m.get('description', '')}")

        def load_selected():
            selection = lb.curselection()
            if not selection or selection[0] >= len(missions):
                return
            name = missions[selection[0]].get("name")
            dialog.destroy()
            self._download_remote_mission(base_url, name)

        ttk.Button(dialog, text="Ausgewaehlte Mission laden", command=load_selected).pack(pady=(0, 8))

    def _download_remote_mission(self, base_url, name):
        def worker():
            self._set_status(f"Lade Mission '{name}' vom Pico...")
            try:
                mission = fw._get_json(base_url + "/mission-download?name=" + name, timeout=10)
            except Exception as e:
                self._set_status(f"Download fehlgeschlagen: {e}", error=True)
                return
            self.after(0, lambda: self._load_mission_into_form(mission))
            self.current_path = None
            self._set_status(f"Vom Pico geladen: {name} (noch nicht lokal gespeichert)")

        threading.Thread(target=worker, daemon=True).start()

    def _detect_ports(self):
        def worker():
            self._set_status("Suche serielle Ports...")
            try:
                mpremote_cmd = fw._resolve_mpremote_command()
                ports = fw.auto_detect_pico_ports(mpremote_cmd)
            except Exception as e:
                self._set_status(f"Port-Suche fehlgeschlagen: {e}", error=True)
                return
            self.after(0, lambda: self._apply_detected_ports(ports))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_detected_ports(self, ports):
        self.port_combo["values"] = ports
        if ports and not self.port_var.get():
            self.port_var.set(ports[0])
        self._set_status(f"{len(ports)} Port(s) gefunden." if ports else "Kein Port gefunden.")

    def _upload_serial(self):
        try:
            mission = self._collect_mission_from_form()
        except ValueError as e:
            messagebox.showerror(APP_TITLE, str(e))
            return
        ok, err = validate_mission(mission)
        if not ok:
            messagebox.showerror(APP_TITLE, err)
            return
        port = self.port_var.get().strip()
        if not port:
            messagebox.showinfo(APP_TITLE, "Bitte zuerst einen Port waehlen (oder 'Ports suchen').")
            return
        safe_name = sanitize_mission_name(mission["name"])
        if not safe_name:
            messagebox.showerror(APP_TITLE, "Ungueltiger Name.")
            return
        remote_name = safe_name + MISSION_EXTENSION

        def worker():
            self._set_status(f"Hochladen per Seriell ({port}) laeuft...")
            tmp_path = None
            try:
                mpremote_cmd = fw._resolve_mpremote_command()
                with tempfile.NamedTemporaryFile("w", suffix=MISSION_EXTENSION, delete=False, encoding="utf-8") as tf:
                    tf.write(json.dumps(mission))
                    tmp_path = tf.name
                fw._run_mpremote(mpremote_cmd, ["connect", port, "cp", tmp_path, ":" + remote_name], timeout=60)
                self._set_status(f"Seriell-Upload OK: {remote_name} -> {port}")
            except Exception as e:
                self._set_status(f"Seriell-Upload fehlgeschlagen: {e}", error=True)
            finally:
                if tmp_path:
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

        threading.Thread(target=worker, daemon=True).start()


def main():
    app = MissionBuilderApp()
    app.mainloop()


if __name__ == "__main__":
    main()
