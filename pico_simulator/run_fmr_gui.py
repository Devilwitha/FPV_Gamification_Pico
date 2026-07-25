import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from tkinter.scrolledtext import ScrolledText

from run_firmware import SIM_PROFILES_PATH, load_sim_profiles, save_sim_profiles


class SimulatorGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FPV Pico Simulator Launcher")
        self.geometry("760x520")
        self.minsize(720, 500)

        self.process = None
        self.log_queue = queue.Queue()
        self.project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.run_fmr_path = os.path.join(self.project_root, "pico_simulator", "run_fmr.py")
        self.profile_file_path = SIM_PROFILES_PATH
        self.sim_profiles = load_sim_profiles(self.profile_file_path)

        self._build_ui()
        self._refresh_profile_list(selected="pico_w")
        self.after(400, self._poll_process)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        row = 0
        ttk.Label(frame, text="Entry").grid(row=row, column=0, sticky="w")
        self.entry_var = tk.StringVar(value="boot")
        entry_cb = ttk.Combobox(frame, textvariable=self.entry_var, values=["main", "boot", "recovery"], state="readonly")
        entry_cb.grid(row=row, column=1, sticky="ew", padx=(8, 0))

        row += 1
        ttk.Label(frame, text="Sim Profile").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.profile_var = tk.StringVar(value="pico_w")
        self.profile_cb = ttk.Combobox(
            frame,
            textvariable=self.profile_var,
            values=[],
            state="normal",
        )
        self.profile_cb.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))
        self.profile_cb.bind("<<ComboboxSelected>>", lambda _e: self._apply_profile_defaults())

        row += 1
        profile_btn_row = ttk.Frame(frame)
        profile_btn_row.grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Button(profile_btn_row, text="Profil laden", command=self._apply_profile_defaults).pack(side="left")
        ttk.Button(profile_btn_row, text="Profil speichern", command=self._save_profile).pack(side="left", padx=(8, 0))
        ttk.Button(profile_btn_row, text="Neu anlegen", command=self._create_profile).pack(side="left", padx=(8, 0))
        ttk.Button(profile_btn_row, text="Profil loeschen", command=self._delete_profile).pack(side="left", padx=(8, 0))

        row += 1
        ttk.Label(frame, text="Port").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.port_var = tk.StringVar(value="8080")
        ttk.Entry(frame, textvariable=self.port_var).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        row += 1
        ttk.Label(frame, text="Source Dir").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.source_var = tk.StringVar(value=os.path.join(self.project_root, "source"))
        ttk.Entry(frame, textvariable=self.source_var).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        row += 1
        ttk.Label(frame, text="Data Dir").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.data_var = tk.StringVar(value=os.path.join(self.project_root, "data"))
        ttk.Entry(frame, textvariable=self.data_var).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        row += 1
        self.refresh_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Refresh data before start", variable=self.refresh_var).grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))

        row += 1
        sep = ttk.Separator(frame, orient="horizontal")
        sep.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 10))

        row += 1
        ttk.Label(frame, text="mem_free (KB)").grid(row=row, column=0, sticky="w")
        self.mem_free_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.mem_free_var).grid(row=row, column=1, sticky="ew", padx=(8, 0))

        row += 1
        ttk.Label(frame, text="mem_alloc (KB)").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.mem_alloc_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.mem_alloc_var).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        row += 1
        ttk.Label(frame, text="CPU freq (MHz)").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.cpu_freq_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.cpu_freq_var).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        row += 1
        ttk.Label(frame, text="CPU scale").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.cpu_scale_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.cpu_scale_var).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        row += 1
        ttk.Label(frame, text="Net latency (ms)").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.net_latency_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.net_latency_var).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        row += 1
        ttk.Label(frame, text="Real RAM limit (MB)").grid(row=row, column=0, sticky="w", pady=(8, 0))
        self.real_ram_limit_var = tk.StringVar()
        ttk.Entry(frame, textvariable=self.real_ram_limit_var).grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=(8, 0))

        row += 1
        btn_row = ttk.Frame(frame)
        btn_row.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        self.start_btn = ttk.Button(btn_row, text="Start run_fmr", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(btn_row, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        row += 1
        self.status_var = tk.StringVar(value="Bereit.")
        ttk.Label(frame, textvariable=self.status_var).grid(row=row, column=0, columnspan=2, sticky="w", pady=(12, 0))

        row += 1
        ttk.Label(frame, text="Command preview").grid(row=row, column=0, columnspan=2, sticky="w", pady=(8, 0))

        row += 1
        self.cmd_var = tk.StringVar(value="")
        cmd_entry = ttk.Entry(frame, textvariable=self.cmd_var, state="readonly")
        cmd_entry.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        row += 1
        log_row = ttk.Frame(frame)
        log_row.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Label(log_row, text="Terminal Output").pack(side="left")
        ttk.Button(log_row, text="Clear", command=self._clear_log).pack(side="right")

        row += 1
        self.log_widget = ScrolledText(frame, height=10, wrap="word", state="disabled")
        self.log_widget.grid(row=row, column=0, columnspan=2, sticky="nsew", pady=(4, 0))

        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(row, weight=1)

    def _clear_log(self):
        self.log_widget.configure(state="normal")
        self.log_widget.delete("1.0", tk.END)
        self.log_widget.configure(state="disabled")

    def _append_log(self, text):
        self.log_widget.configure(state="normal")
        self.log_widget.insert(tk.END, text)
        self.log_widget.see(tk.END)
        self.log_widget.configure(state="disabled")

    def _read_process_output(self, process):
        if not process.stdout:
            return
        try:
            for line in process.stdout:
                self.log_queue.put(line)
        finally:
            try:
                process.stdout.close()
            except Exception:
                pass

    def _refresh_profile_list(self, selected=None):
        names = sorted(self.sim_profiles.keys())
        self.profile_cb.configure(values=names)
        if selected and selected in self.sim_profiles:
            self.profile_var.set(selected)
        elif self.profile_var.get() in self.sim_profiles:
            pass
        elif names:
            self.profile_var.set(names[0])
        self._apply_profile_defaults()

    def _apply_profile_defaults(self):
        profile_name = self.profile_var.get()
        profile = self.sim_profiles.get(profile_name)
        if not profile:
            messagebox.showwarning("Profil nicht gefunden", f"Profil '{profile_name}' existiert nicht.")
            return
        self.mem_free_var.set(str(profile["mem_free_kb"]))
        self.mem_alloc_var.set(str(profile["mem_alloc_kb"]))
        self.cpu_freq_var.set(str(profile["cpu_freq_mhz"]))
        self.cpu_scale_var.set(str(profile["cpu_scale"]))
        self.net_latency_var.set(str(profile["net_latency_ms"]))
        limit = profile.get("real_ram_limit_mb")
        self.real_ram_limit_var.set("" if limit in (None, "") else str(limit))

    def _collect_profile_values(self):
        real_limit_text = self.real_ram_limit_var.get().strip()
        return {
            "mem_free_kb": int(self.mem_free_var.get().strip()),
            "mem_alloc_kb": int(self.mem_alloc_var.get().strip()),
            "cpu_freq_mhz": int(self.cpu_freq_var.get().strip()),
            "cpu_scale": float(self.cpu_scale_var.get().strip()),
            "net_latency_ms": int(self.net_latency_var.get().strip()),
            "real_ram_limit_mb": int(real_limit_text) if real_limit_text else None,
        }

    def _save_profile(self):
        profile_name = self.profile_var.get().strip()
        if not profile_name:
            messagebox.showerror("Fehlender Name", "Bitte Profilnamen eingeben.")
            return

        try:
            values = self._collect_profile_values()
        except ValueError:
            messagebox.showerror("Ungueltige Werte", "Bitte nur gueltige Zahlenwerte fuer das Profil eintragen.")
            return

        self.sim_profiles[profile_name] = values
        save_sim_profiles(self.profile_file_path, self.sim_profiles)
        self.sim_profiles = load_sim_profiles(self.profile_file_path)
        self._refresh_profile_list(selected=profile_name)
        self.status_var.set(f"Profil '{profile_name}' gespeichert.")

    def _create_profile(self):
        name = simpledialog.askstring("Neues Profil", "Name fuer neues Profil:", parent=self)
        if not name:
            return
        profile_name = name.strip()
        if not profile_name:
            return

        if profile_name in self.sim_profiles:
            messagebox.showwarning("Existiert bereits", f"Profil '{profile_name}' gibt es schon.")
            return

        try:
            values = self._collect_profile_values()
        except ValueError:
            messagebox.showerror("Ungueltige Werte", "Bitte zuerst gueltige Zahlenwerte eintragen.")
            return

        self.sim_profiles[profile_name] = values
        save_sim_profiles(self.profile_file_path, self.sim_profiles)
        self.sim_profiles = load_sim_profiles(self.profile_file_path)
        self._refresh_profile_list(selected=profile_name)
        self.status_var.set(f"Neues Profil '{profile_name}' angelegt.")

    def _delete_profile(self):
        profile_name = self.profile_var.get().strip()
        if profile_name == "pico_w":
            messagebox.showwarning("Nicht erlaubt", "Das Basisprofil 'pico_w' kann nicht geloescht werden.")
            return
        if profile_name not in self.sim_profiles:
            messagebox.showwarning("Nicht gefunden", f"Profil '{profile_name}' existiert nicht.")
            return

        if not messagebox.askyesno("Profil loeschen", f"Profil '{profile_name}' wirklich loeschen?"):
            return

        del self.sim_profiles[profile_name]
        save_sim_profiles(self.profile_file_path, self.sim_profiles)
        self.sim_profiles = load_sim_profiles(self.profile_file_path)
        self._refresh_profile_list(selected="pico_w")
        self.status_var.set(f"Profil '{profile_name}' geloescht.")

    def _build_command(self):
        profile_name = self.profile_var.get().strip()
        if profile_name not in self.sim_profiles:
            raise ValueError(f"Profil '{profile_name}' wurde nicht gefunden. Erst speichern oder laden.")

        cmd = [
            sys.executable,
            "-u",
            self.run_fmr_path,
            "--entry",
            self.entry_var.get().strip(),
            "--sim-profile",
            profile_name,
            "--port",
            self.port_var.get().strip(),
            "--source-dir",
            self.source_var.get().strip(),
            "--data-dir",
            self.data_var.get().strip(),
        ]

        if self.refresh_var.get():
            cmd.append("--refresh-data")

        self._append_if_value(cmd, "--mem-free-kb", self.mem_free_var.get())
        self._append_if_value(cmd, "--mem-alloc-kb", self.mem_alloc_var.get())
        self._append_if_value(cmd, "--cpu-freq-mhz", self.cpu_freq_var.get())
        self._append_if_value(cmd, "--cpu-scale", self.cpu_scale_var.get())
        self._append_if_value(cmd, "--net-latency-ms", self.net_latency_var.get())
        self._append_if_value(cmd, "--real-ram-limit-mb", self.real_ram_limit_var.get())

        return cmd

    @staticmethod
    def _append_if_value(cmd, flag, value):
        text = (value or "").strip()
        if text:
            cmd.extend([flag, text])

    def _start(self):
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Bereits aktiv", "run_fmr laeuft bereits.")
            return

        try:
            cmd = self._build_command()
            self.cmd_var.set(" ".join(cmd))
            self._append_log("\n=== run_fmr start ===\n")
            self._append_log("$ " + " ".join(cmd) + "\n")
            self.process = subprocess.Popen(
                cmd,
                cwd=self.project_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            threading.Thread(target=self._read_process_output, args=(self.process,), daemon=True).start()
            self.status_var.set(f"Gestartet (PID {self.process.pid}).")
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
        except Exception as exc:
            messagebox.showerror("Start fehlgeschlagen", str(exc))
            self.status_var.set(f"Fehler: {exc}")

    def _stop(self):
        if not self.process or self.process.poll() is not None:
            self.status_var.set("Keine laufende Instanz.")
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            return

        try:
            self.process.terminate()
            self.status_var.set("Stop-Signal gesendet.")
        except Exception as exc:
            messagebox.showerror("Stop fehlgeschlagen", str(exc))

    def _poll_process(self):
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(line)

        if self.process:
            exit_code = self.process.poll()
            if exit_code is not None:
                self.status_var.set(f"Beendet mit Exit-Code {exit_code}.")
                self._append_log(f"=== run_fmr beendet mit Exit-Code {exit_code} ===\n")
                self.start_btn.config(state="normal")
                self.stop_btn.config(state="disabled")
                self.process = None
        self.after(500, self._poll_process)

    def _on_close(self):
        if self.process and self.process.poll() is None:
            if not messagebox.askyesno("Beenden", "run_fmr laeuft noch. Trotzdem beenden?"):
                return
            try:
                self.process.terminate()
            except Exception:
                pass
        self.destroy()


def main():
    app = SimulatorGui()
    app.mainloop()


if __name__ == "__main__":
    main()
