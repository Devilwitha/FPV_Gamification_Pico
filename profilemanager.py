import json
from pathlib import Path
from typing import Optional
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

APP_TITLE = "Profile Manager"
PROFILE_EXTENSION = ".pro"

REQUIRED_KEYS = [
    "gyro_trick_threshold",
    "stable_threshold",
    "trick_start_hold_ms",
    "stable_hold_ms",
    "gyro_deadband",
    "gyro_lowpass_alpha",
    "min_trick_duration",
    "trick_min_accum_deg",
    "trick_spin_min_accum_deg",
    "trick_axis_dominance_ratio",
    "trick_start_type_weight",
]

DEFAULT_SETTINGS = {
    "gyro_trick_threshold": 190,
    "stable_threshold": 65,
    "trick_start_hold_ms": 35,
    "stable_hold_ms": 140,
    "gyro_deadband": 12,
    "gyro_lowpass_alpha": 0.30,
    "min_trick_duration": 0.12,
    "trick_min_accum_deg": 80,
    "trick_spin_min_accum_deg": 120,
    "trick_axis_dominance_ratio": 1.18,
    "trick_start_type_weight": 0.92,
}


def default_profile_flat() -> dict:
    """Returns a flat settings dict (Pico-compatible format)."""
    return dict(DEFAULT_SETTINGS)


def _normalize_flat(data: dict) -> dict:
    """Extract flat settings from either flat or {name, settings} format."""
    if "settings" in data and isinstance(data["settings"], dict):
        data = data["settings"]
    normalized = dict(DEFAULT_SETTINGS)
    for key, default_value in DEFAULT_SETTINGS.items():
        if key in data:
            value = data[key]
            if isinstance(default_value, float):
                normalized[key] = float(value)
            else:
                normalized[key] = int(value)
    return normalized


class ProfileManagerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1100x720")
        self.minsize(900, 620)

        self.current_path: Optional[Path] = None
        self.current_data: dict = default_profile_flat()
        self.dirty = False

        self._build_ui()
        self._refresh_file_list()
        self._load_default_profile()

    def _build_ui(self):
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self, padding=10)
        sidebar.grid(row=0, column=0, sticky="ns")
        sidebar.rowconfigure(1, weight=1)

        ttk.Label(sidebar, text="Profiles", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 6))

        list_frame = ttk.Frame(sidebar)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)

        self.file_list = tk.Listbox(list_frame, width=30, height=25)
        self.file_list.grid(row=0, column=0, sticky="nsew")
        self.file_list.bind("<<ListboxSelect>>", self._on_file_selected)

        file_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_list.yview)
        file_scroll.grid(row=0, column=1, sticky="ns")
        self.file_list.configure(yscrollcommand=file_scroll.set)

        sidebar_buttons = ttk.Frame(sidebar)
        sidebar_buttons.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        sidebar_buttons.columnconfigure(0, weight=1)

        ttk.Button(sidebar_buttons, text="Neu", command=self.new_profile).grid(row=0, column=0, sticky="ew", pady=2)
        ttk.Button(sidebar_buttons, text="Öffnen", command=self.open_profile_dialog).grid(row=1, column=0, sticky="ew", pady=2)
        ttk.Button(sidebar_buttons, text="Speichern", command=self.save_profile).grid(row=2, column=0, sticky="ew", pady=2)
        ttk.Button(sidebar_buttons, text="Speichern unter", command=self.save_profile_as).grid(row=3, column=0, sticky="ew", pady=2)
        ttk.Button(sidebar_buttons, text="Aktualisieren", command=self._refresh_file_list).grid(row=4, column=0, sticky="ew", pady=2)

        editor = ttk.Frame(self, padding=10)
        editor.grid(row=0, column=1, sticky="nsew")
        editor.rowconfigure(3, weight=1)
        editor.columnconfigure(1, weight=1)

        ttk.Label(editor, text="Datei", font=("Segoe UI", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.path_var = tk.StringVar(value="Unbenannt")
        ttk.Entry(editor, textvariable=self.path_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=(8, 0))

        ttk.Label(editor, text="Profilname (= Dateiname)", font=("Segoe UI", 10, "bold")).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.name_var = tk.StringVar()
        ttk.Entry(editor, textvariable=self.name_var).grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        ttk.Label(editor, text="Settings (JSON)", font=("Segoe UI", 10, "bold")).grid(row=2, column=0, sticky="nw", pady=(10, 0))
        json_frame = ttk.Frame(editor)
        json_frame.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=(8, 0))
        json_frame.rowconfigure(0, weight=1)
        json_frame.columnconfigure(0, weight=1)

        self.text = tk.Text(json_frame, wrap="none", undo=True, font=("Consolas", 10))
        self.text.grid(row=0, column=0, sticky="nsew")
        self.text.bind("<<Modified>>", self._on_text_modified)

        y_scroll = ttk.Scrollbar(json_frame, orient="vertical", command=self.text.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(json_frame, orient="horizontal", command=self.text.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        bottom = ttk.Frame(editor)
        bottom.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        bottom.columnconfigure(0, weight=1)

        self.status_var = tk.StringVar(value="Bereit.")
        ttk.Label(bottom, textvariable=self.status_var).grid(row=0, column=0, sticky="w")

        action_row = ttk.Frame(bottom)
        action_row.grid(row=0, column=1, sticky="e")
        ttk.Button(action_row, text="Template einfügen", command=self.insert_template).grid(row=0, column=0, padx=4)
        ttk.Button(action_row, text="JSON prüfen", command=self.validate_json).grid(row=0, column=1, padx=4)

        self._set_editor(default_profile_flat())

    def _set_status(self, text: str):
        self.status_var.set(text)

    def _refresh_file_list(self):
        self.file_list.delete(0, tk.END)
        try:
            files = sorted(
                [p for p in Path.cwd().iterdir() if p.is_file() and p.suffix.lower() == PROFILE_EXTENSION]
            )
            for path in files:
                self.file_list.insert(tk.END, path.name)
            self._set_status(f"{len(files)} .pro Datei(en) gefunden.")
        except Exception as exc:
            self._set_status(f"Fehler beim Laden der Dateiliste: {exc}")

    def _set_editor(self, data: dict, path: Optional[Path] = None):
        self.current_data = data
        self.current_path = path
        self.dirty = False
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", json.dumps(data, indent=2, ensure_ascii=False))
        self.text.edit_modified(False)
        # Profilname = Dateiname ohne Extension
        name = path.stem if path else self.name_var.get() or "freestyle"
        self.name_var.set(name)
        self.path_var.set(str(path) if path else "Unbenannt")
        self._set_status("Geladen.")

    def _load_default_profile(self):
        self._set_editor(default_profile_flat())

    def _selected_file_path(self) -> Optional[Path]:
        selection = self.file_list.curselection()
        if not selection:
            return None
        return Path.cwd() / self.file_list.get(selection[0])

    def _on_file_selected(self, event=None):
        path = self._selected_file_path()
        if not path:
            return
        self.open_profile(path)

    def _on_text_modified(self, event=None):
        if self.text.edit_modified():
            self.dirty = True
            self._set_status("Ungesicherte Änderungen.")
            self.text.edit_modified(False)

    def _read_editor_data(self) -> dict:
        raw = self.text.get("1.0", tk.END).strip()
        if not raw:
            raise ValueError("Editor ist leer.")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Top-Level JSON muss ein Objekt sein.")
        return data


    def open_profile(self, path: Path):
        try:
            content = path.read_text(encoding="utf-8")
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("Datei enthält kein JSON-Objekt.")
            flat = _normalize_flat(data)
            self._set_editor(flat, path)
            self._set_status(f"Geöffnet: {path.name}")
        except Exception as exc:
            messagebox.showerror("Öffnen fehlgeschlagen", str(exc))
            self._set_status(f"Fehler beim Öffnen: {exc}")

    def open_profile_dialog(self):
        filename = filedialog.askopenfilename(
            title=".pro Datei öffnen",
            filetypes=[("Profile", "*.pro"), ("Alle Dateien", "*.*")],
        )
        if filename:
            self.open_profile(Path(filename))

    def new_profile(self):
        self._set_editor(default_profile_flat())
        self.name_var.set("freestyle")
        self._set_status("Neues Profil erstellt.")

    def insert_template(self):
        self._set_editor(default_profile_flat(), self.current_path)
        self._set_status("Vorlage eingefügt.")

    def validate_json(self):
        try:
            data = self._read_editor_data()
            flat = _normalize_flat(data)
            self._set_editor(flat, self.current_path)
            self._set_status("JSON ist gültig.")
            messagebox.showinfo("OK", "JSON ist gültig.")
        except Exception as exc:
            messagebox.showerror("JSON ungültig", str(exc))
            self._set_status(f"JSON ungültig: {exc}")

    def _current_save_path(self) -> Path:
        if self.current_path is not None:
            return self.current_path
        name = self.name_var.get().strip() or "freestyle"
        safe_name = "".join(ch for ch in name.lower().replace(" ", "_") if ch.isalnum() or ch in ("_", "-"))
        if not safe_name:
            safe_name = "freestyle"
        return Path.cwd() / f"{safe_name}{PROFILE_EXTENSION}"

    def save_profile(self):
        try:
            data = self._read_editor_data()
            flat = _normalize_flat(data)
            path = self._current_save_path()
            path.write_text(json.dumps(flat, indent=2, ensure_ascii=False), encoding="utf-8")
            self._set_editor(flat, path)
            self._refresh_file_list()
            self._set_status(f"Gespeichert: {path.name}")
        except Exception as exc:
            messagebox.showerror("Speichern fehlgeschlagen", str(exc))
            self._set_status(f"Fehler beim Speichern: {exc}")

    def save_profile_as(self):
        filename = filedialog.asksaveasfilename(
            title="Profil speichern unter",
            defaultextension=PROFILE_EXTENSION,
            filetypes=[("Profile", "*.pro")],
            initialfile=self._current_save_path().name,
        )
        if not filename:
            return
        try:
            data = self._read_editor_data()
            flat = _normalize_flat(data)
            path = Path(filename)
            if path.suffix.lower() != PROFILE_EXTENSION:
                path = path.with_suffix(PROFILE_EXTENSION)
            path.write_text(json.dumps(flat, indent=2, ensure_ascii=False), encoding="utf-8")
            self._set_editor(flat, path)
            self._refresh_file_list()
            self._set_status(f"Gespeichert: {path.name}")
        except Exception as exc:
            messagebox.showerror("Speichern fehlgeschlagen", str(exc))
            self._set_status(f"Fehler beim Speichern unter: {exc}")


def main():
    app = ProfileManagerApp()
    app.mainloop()


if __name__ == "__main__":
    main()
