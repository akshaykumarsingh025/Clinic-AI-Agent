import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, filedialog, messagebox
from tkinter import scrolledtext, ttk
from urllib.parse import urlparse
from urllib.request import urlopen, Request
from urllib.error import URLError


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
BOT_DIR = BASE_DIR / "whatsapp-bot"
AUTH_DIR = BOT_DIR / "auth_info"

FIELD_ORDER = [
    "CLINIC_PHONE",
    "APPOINTMENT_FEE",
    "CLINIC_TIMEZONE",
    "SLOT_DURATION_MINUTES",
    "MORNING_START",
    "MORNING_END",
    "EVENING_START",
    "EVENING_END",
    "WORKING_DAYS",
    "OLLAMA_MODEL",
    "OLLAMA_HOST",
    "FASTAPI_HOST",
    "FASTAPI_PORT",
    "WHATSAPP_BOT_URL",
    "TTS_PROVIDER",
    "SEND_AUDIO_REPLIES_FOR_TEXT",
    "PIPER_BINARY",
    "PIPER_VOICE",
    "VOICECLONE_PROJECT_DIR",
    "VOICECLONE_PYTHON",
    "VOICECLONE_VOICE_SAMPLE",
    "VOICECLONE_LANGUAGE",
    "VOICECLONE_REF_TEXT",
    "EXPORT_DIR",
    "GOOGLE_SHEET_URL",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
]

DEFAULTS = {
    "CLINIC_PHONE": "+91XXXXXXXXXX",
    "APPOINTMENT_FEE": "Please confirm with the clinic",
    "CLINIC_TIMEZONE": "Asia/Kolkata",
    "SLOT_DURATION_MINUTES": "20",
    "MORNING_START": "10:00",
    "MORNING_END": "13:00",
    "EVENING_START": "17:00",
    "EVENING_END": "20:00",
    "WORKING_DAYS": "Mon,Tue,Wed,Thu,Fri,Sat",
    "OLLAMA_MODEL": "gemma4:e4b",
    "OLLAMA_HOST": "http://localhost:11434",
    "FASTAPI_HOST": "0.0.0.0",
    "FASTAPI_PORT": "8000",
    "WHATSAPP_BOT_URL": "http://localhost:3001",
    "TTS_PROVIDER": "piper",
    "SEND_AUDIO_REPLIES_FOR_TEXT": "false",
    "PIPER_BINARY": "piper",
    "PIPER_VOICE": "./voices/en_IN-female-medium.onnx",
    "VOICECLONE_PROJECT_DIR": "D:/Software/Projects/VoiceCloneReels",
    "VOICECLONE_PYTHON": "",
    "VOICECLONE_VOICE_SAMPLE": "",
    "VOICECLONE_LANGUAGE": "auto",
    "VOICECLONE_REF_TEXT": "",
    "EXPORT_DIR": "./exports",
    "GOOGLE_SHEET_URL": "",
    "GOOGLE_SERVICE_ACCOUNT_JSON": "",
}


def load_env() -> dict[str, str]:
    values = DEFAULTS.copy()
    if not ENV_PATH.exists():
        return values
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def save_env(values: dict[str, str]):
    existing_lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    written = set()
    new_lines = []

    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in values:
            new_lines.append(f"{key}={values[key]}")
            written.add(key)
        else:
            new_lines.append(line)

    for key in FIELD_ORDER:
        if key in values and key not in written:
            new_lines.append(f"{key}={values[key]}")

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")


class ClinicControlApp(Tk):
    def __init__(self):
        super().__init__()
        self.title("Dr. Deepika Appointment Assistant Control")
        self.geometry("1120x760")
        self.minsize(980, 660)

        self.env_values = load_env()
        self.vars: dict[str, StringVar | BooleanVar] = {}
        self.backend_proc: subprocess.Popen | None = None
        self.bot_proc: subprocess.Popen | None = None
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.latest_export_path: str | None = None

        self._build_ui()
        self.after(200, self._drain_logs)

    def _build_ui(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

        self.services_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.appointments_tab = ttk.Frame(self.notebook)
        self.today_tab = ttk.Frame(self.notebook)
        self.noshow_tab = ttk.Frame(self.notebook)
        self.block_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.services_tab, text="Services")
        self.notebook.add(self.settings_tab, text="Settings")
        self.notebook.add(self.appointments_tab, text="Appointments")
        self.notebook.add(self.today_tab, text="Today")
        self.notebook.add(self.noshow_tab, text="No-Shows")
        self.notebook.add(self.block_tab, text="Block Slots")

        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_services_tab()
        self._build_settings_tab()
        self._build_appointments_tab()
        self._build_today_tab()
        self._build_noshow_tab()
        self._build_block_tab()

    def _build_services_tab(self):
        controls = ttk.LabelFrame(self.services_tab, text="Run Controls")
        controls.pack(fill="x", padx=10, pady=10)

        ttk.Button(controls, text="Start Backend", command=self.start_backend).grid(row=0, column=0, padx=6, pady=8)
        ttk.Button(controls, text="Stop Backend", command=self.stop_backend).grid(row=0, column=1, padx=6, pady=8)
        ttk.Button(controls, text="Start WhatsApp Bot", command=self.start_bot).grid(row=0, column=2, padx=6, pady=8)
        ttk.Button(controls, text="Stop WhatsApp Bot", command=self.stop_bot).grid(row=0, column=3, padx=6, pady=8)
        ttk.Button(controls, text="Start All", command=self.start_all).grid(row=0, column=4, padx=6, pady=8)
        ttk.Button(controls, text="Stop All", command=self.stop_all).grid(row=0, column=5, padx=6, pady=8)

        ttk.Button(
            controls,
            text="Use New WhatsApp Number",
            command=self.reset_whatsapp_login,
        ).grid(row=1, column=0, columnspan=2, padx=6, pady=8, sticky="ew")
        ttk.Button(controls, text="Refresh Status", command=self.refresh_status).grid(row=1, column=2, padx=6, pady=8)

        self.backend_status = StringVar(value="Backend: stopped")
        self.bot_status = StringVar(value="WhatsApp: stopped")
        ttk.Label(controls, textvariable=self.backend_status).grid(row=1, column=3, padx=6, pady=8, sticky="w")
        ttk.Label(controls, textvariable=self.bot_status).grid(row=1, column=4, columnspan=2, padx=6, pady=8, sticky="w")

        hint = ttk.Label(
            self.services_tab,
            text="To scan a new number: stop the bot, click 'Use New WhatsApp Number', then scan the QR shown in the log below.",
        )
        hint.pack(anchor="w", padx=14, pady=(0, 6))

        log_frame = ttk.LabelFrame(self.services_tab, text="Service Log / WhatsApp QR")
        log_frame.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap="none", font=("Consolas", 10))
        self.log_text.pack(fill="both", expand=True, padx=6, pady=6)

    def _setting_row(self, parent, row: int, key: str, label: str, browse: str | None = None, width: int = 58):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=5)
        var = StringVar(value=self.env_values.get(key, DEFAULTS.get(key, "")))
        self.vars[key] = var
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=1, sticky="ew", padx=6, pady=5)
        if browse:
            ttk.Button(parent, text="Browse", command=lambda: self._browse_for(key, browse)).grid(row=row, column=2, padx=6, pady=5)
        return entry

    def _build_settings_tab(self):
        outer = ttk.Frame(self.settings_tab)
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        outer.columnconfigure(0, weight=1)

        clinic = ttk.LabelFrame(outer, text="Clinic and Model")
        clinic.grid(row=0, column=0, sticky="ew", pady=8)
        clinic.columnconfigure(1, weight=1)
        self._setting_row(clinic, 0, "CLINIC_PHONE", "Clinic phone")
        self._setting_row(clinic, 1, "APPOINTMENT_FEE", "Appointment fee")
        self._setting_row(clinic, 2, "CLINIC_TIMEZONE", "Clinic timezone")

        # Ollama model dropdown
        ttk.Label(clinic, text="Ollama model").grid(row=3, column=0, sticky="w", padx=6, pady=5)
        model_var = StringVar(value=self.env_values.get("OLLAMA_MODEL", DEFAULTS.get("OLLAMA_MODEL", "")))
        self.vars["OLLAMA_MODEL"] = model_var
        self.model_combo = ttk.Combobox(clinic, textvariable=model_var, state="readonly", width=35)
        self.model_combo.grid(row=3, column=1, sticky="w", padx=6, pady=5)
        ttk.Button(clinic, text="Refresh", command=self._refresh_ollama_models).grid(row=3, column=2, padx=6, pady=5)
        self._refresh_ollama_models()  # Load models on startup

        self._setting_row(clinic, 4, "OLLAMA_HOST", "Ollama host")
        self._setting_row(clinic, 5, "FASTAPI_PORT", "Backend port", width=16)
        self._setting_row(clinic, 6, "WHATSAPP_BOT_URL", "WhatsApp bot URL")

        timings = ttk.LabelFrame(outer, text="Clinic Timings")
        timings.grid(row=1, column=0, sticky="ew", pady=8)
        timings.columnconfigure(1, weight=1)
        self._setting_row(timings, 0, "MORNING_START", "Morning start", width=16)
        self._setting_row(timings, 1, "MORNING_END", "Morning end", width=16)
        self._setting_row(timings, 2, "EVENING_START", "Evening start", width=16)
        self._setting_row(timings, 3, "EVENING_END", "Evening end", width=16)
        self._setting_row(timings, 4, "WORKING_DAYS", "Working days (e.g. Mon,Tue,Wed)")
        self._setting_row(timings, 5, "SLOT_DURATION_MINUTES", "Slot duration (mins)", width=16)

        voice = ttk.LabelFrame(outer, text="Voice Replies")
        voice.grid(row=2, column=0, sticky="ew", pady=8)
        voice.columnconfigure(1, weight=1)

        ttk.Label(voice, text="TTS provider").grid(row=0, column=0, sticky="w", padx=6, pady=5)
        provider = StringVar(value=self.env_values.get("TTS_PROVIDER", "piper"))
        self.vars["TTS_PROVIDER"] = provider
        ttk.Combobox(voice, textvariable=provider, values=["piper", "voiceclone"], state="readonly", width=20).grid(row=0, column=1, sticky="w", padx=6, pady=5)

        audio_text = BooleanVar(value=self.env_values.get("SEND_AUDIO_REPLIES_FOR_TEXT", "false").lower() in ("1", "true", "yes", "on"))
        self.vars["SEND_AUDIO_REPLIES_FOR_TEXT"] = audio_text
        ttk.Checkbutton(voice, text="Also send audio replies for typed messages", variable=audio_text).grid(row=1, column=1, sticky="w", padx=6, pady=5)

        self._setting_row(voice, 2, "PIPER_BINARY", "Piper binary")
        self._setting_row(voice, 3, "PIPER_VOICE", "Piper voice model", browse="file")
        self._setting_row(voice, 4, "VOICECLONE_PROJECT_DIR", "VoiceCloneReels folder", browse="dir")
        self._setting_row(voice, 5, "VOICECLONE_PYTHON", "VoiceClone Python", browse="file")
        self._setting_row(voice, 6, "VOICECLONE_VOICE_SAMPLE", "Assistant voice sample", browse="file")

        ttk.Label(voice, text="VoiceClone language").grid(row=7, column=0, sticky="w", padx=6, pady=5)
        voice_lang = StringVar(value=self.env_values.get("VOICECLONE_LANGUAGE", "auto"))
        self.vars["VOICECLONE_LANGUAGE"] = voice_lang
        ttk.Combobox(voice, textvariable=voice_lang, values=["auto", "English", "Hindi"], state="readonly", width=20).grid(row=7, column=1, sticky="w", padx=6, pady=5)

        self._setting_row(voice, 8, "VOICECLONE_REF_TEXT", "Reference transcript")

        sheets = ttk.LabelFrame(outer, text="Excel and Google Sheets")
        sheets.grid(row=3, column=0, sticky="ew", pady=8)
        sheets.columnconfigure(1, weight=1)
        self._setting_row(sheets, 0, "EXPORT_DIR", "Excel export folder", browse="dir")
        self._setting_row(sheets, 1, "GOOGLE_SHEET_URL", "Google Sheet URL")
        self._setting_row(sheets, 2, "GOOGLE_SERVICE_ACCOUNT_JSON", "Service account JSON", browse="file")

        actions = ttk.Frame(outer)
        actions.grid(row=4, column=0, sticky="ew", pady=10)
        ttk.Button(actions, text="Save Settings", command=self.save_settings).pack(side="left", padx=6)
        ttk.Button(actions, text="Reload From .env", command=self.reload_settings).pack(side="left", padx=6)

    def _refresh_ollama_models(self):
        """Fetch model list from Ollama API and populate the dropdown."""
        current_val = self.vars.get("OLLAMA_MODEL", StringVar()).get()
        ollama_host = self.vars.get("OLLAMA_HOST", StringVar(value="http://localhost:11434")).get()
        models = [current_val] if current_val else []
        try:
            req = Request(f"{ollama_host}/api/tags", method="GET")
            with urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                fetched = [m["name"] for m in data.get("models", [])]
                if fetched:
                    models = fetched
        except Exception:
            pass  # Keep current value as fallback
        self.model_combo["values"] = models
        if current_val and current_val in models:
            self.model_combo.set(current_val)
        elif models:
            self.model_combo.set(models[0])

    def _build_appointments_tab(self):
        actions = ttk.Frame(self.appointments_tab)
        actions.pack(fill="x", padx=10, pady=10)
        ttk.Button(actions, text="Refresh", command=self.load_appointments).pack(side="left", padx=5)
        ttk.Button(actions, text="Export Excel", command=self.export_excel).pack(side="left", padx=5)
        ttk.Button(actions, text="Open Export", command=self.open_latest_export).pack(side="left", padx=5)
        ttk.Button(actions, text="Sync Google Sheet", command=self.sync_google_sheet).pack(side="left", padx=5)

        columns = ("id", "date", "time", "name", "phone", "status", "reason", "id_card")
        self.appointment_tree = ttk.Treeview(self.appointments_tab, columns=columns, show="headings")
        headings = {
            "id": "ID",
            "date": "Date",
            "time": "Time",
            "name": "Patient",
            "phone": "Phone",
            "status": "Status",
            "reason": "Reason",
            "id_card": "ID Card",
        }
        widths = {"id": 60, "date": 110, "time": 80, "name": 170, "phone": 140, "status": 110, "reason": 260, "id_card": 150}
        for column in columns:
            self.appointment_tree.heading(column, text=headings[column])
            self.appointment_tree.column(column, width=widths[column], anchor="w")
        self.appointment_tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.load_appointments()

    def _browse_for(self, key: str, mode: str):
        if mode == "dir":
            selected = filedialog.askdirectory(initialdir=str(BASE_DIR))
        else:
            selected = filedialog.askopenfilename(initialdir=str(BASE_DIR))
        if selected:
            self.vars[key].set(selected.replace("\\", "/"))

    def _collect_settings(self) -> dict[str, str]:
        values = self.env_values.copy()
        for key, var in self.vars.items():
            if isinstance(var, BooleanVar):
                values[key] = "true" if var.get() else "false"
            else:
                values[key] = var.get().strip()
        return values

    def save_settings(self, show_message: bool = True):
        values = self._collect_settings()
        save_env(values)
        self.env_values = values
        if show_message:
            messagebox.showinfo("Saved", "Settings saved. Restart backend/bot for running services to use new values.")

    def reload_settings(self):
        self.env_values = load_env()
        for key, var in self.vars.items():
            if isinstance(var, BooleanVar):
                var.set(self.env_values.get(key, "false").lower() in ("1", "true", "yes", "on"))
            else:
                var.set(self.env_values.get(key, DEFAULTS.get(key, "")))

    def _process_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self._collect_settings())
        env["PYTHONIOENCODING"] = "utf-8"
        env["FASTAPI_URL"] = f"http://localhost:{env.get('FASTAPI_PORT', '8000')}"
        bot_url = env.get("WHATSAPP_BOT_URL", "")
        parsed_bot_url = urlparse(bot_url)
        if parsed_bot_url.port:
            env["BOT_PORT"] = str(parsed_bot_url.port)
        return env

    def _start_process(self, name: str, args: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        proc = subprocess.Popen(
            args,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=flags,
        )
        threading.Thread(target=self._read_process_output, args=(name, proc), daemon=True).start()
        self._log(f"{name} started.")
        return proc

    def _read_process_output(self, name: str, proc: subprocess.Popen):
        if not proc.stdout:
            return
        for line in proc.stdout:
            self.log_queue.put(f"[{name}] {line}")
        self.log_queue.put(f"[{name}] stopped with code {proc.poll()}\n")

    def _drain_logs(self):
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("end", line)
            self.log_text.see("end")
        self.after(200, self._drain_logs)

    def _log(self, text: str):
        self.log_queue.put(text + "\n")

    def _info(self, title: str, message: str):
        self.after(0, lambda: messagebox.showinfo(title, message))

    def _error(self, title: str, message: str):
        self.after(0, lambda: messagebox.showerror(title, message))

    def _warning(self, title: str, message: str):
        self.after(0, lambda: messagebox.showwarning(title, message))

    def start_backend(self):
        if self.backend_proc and self.backend_proc.poll() is None:
            self._log("Backend is already running.")
            return
        env = self._process_env()
        port = env.get("FASTAPI_PORT", "8000")
        host = env.get("FASTAPI_HOST", "0.0.0.0")
        self.backend_proc = self._start_process(
            "backend",
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", host, "--port", port],
            BASE_DIR,
            env,
        )
        self.backend_status.set(f"Backend: starting on {port}")

    def start_bot(self):
        if self.bot_proc and self.bot_proc.poll() is None:
            self._log("WhatsApp bot is already running.")
            return
        env = self._process_env()
        self.bot_proc = self._start_process("whatsapp", ["node", "index.js"], BOT_DIR, env)
        self.bot_status.set("WhatsApp: starting")

    def start_all(self):
        self.save_settings(show_message=False)
        self.start_backend()
        self.start_bot()

    def _stop_process(self, name: str, proc: subprocess.Popen | None):
        if not proc or proc.poll() is not None:
            self._log(f"{name} is not running.")
            return
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        self._log(f"{name} stopped.")

    def stop_backend(self):
        self._stop_process("Backend", self.backend_proc)
        self.backend_status.set("Backend: stopped")

    def stop_bot(self):
        self._stop_process("WhatsApp bot", self.bot_proc)
        self.bot_status.set("WhatsApp: stopped")

    def stop_all(self):
        self.stop_bot()
        self.stop_backend()

    def reset_whatsapp_login(self):
        confirmed = messagebox.askyesno(
            "Use New WhatsApp Number",
            "This will stop the bot and move the current WhatsApp login files to a backup folder. Continue?",
        )
        if not confirmed:
            return
        self.stop_bot()
        if AUTH_DIR.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = BOT_DIR / f"auth_info_backup_{stamp}"
            shutil.move(str(AUTH_DIR), str(backup))
            self._log(f"Old WhatsApp login moved to {backup}")
        else:
            self._log("No existing WhatsApp login folder found.")
        self.start_bot()

    def refresh_status(self):
        def worker():
            values = self._collect_settings()
            port = values.get("FASTAPI_PORT", "8000")
            bot_url = values.get("WHATSAPP_BOT_URL", "http://localhost:3001").rstrip("/")
            try:
                with urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
                    status = response.status
                    self.after(0, lambda status=status: self.backend_status.set(f"Backend: online ({status})"))
            except Exception:
                self.after(0, lambda: self.backend_status.set("Backend: offline"))

            try:
                with urlopen(f"{bot_url}/status", timeout=2) as response:
                    body = response.read().decode("utf-8", errors="replace")
                    snippet = body[:80]
                    self.after(0, lambda snippet=snippet: self.bot_status.set(f"WhatsApp: {snippet}"))
            except Exception:
                self.after(0, lambda: self.bot_status.set("WhatsApp: offline"))

        threading.Thread(target=worker, daemon=True).start()

    def load_appointments(self):
        for row in self.appointment_tree.get_children():
            self.appointment_tree.delete(row)
        try:
            from backend.database import get_all_appointments, init_db
            init_db()
            appointments = get_all_appointments()
        except Exception as exc:
            messagebox.showerror("Appointments", str(exc))
            return

        for appt in appointments:
            self.appointment_tree.insert(
                "",
                "end",
                values=(
                    appt.get("id", ""),
                    appt.get("date", ""),
                    appt.get("time", ""),
                    appt.get("patient_name", ""),
                    appt.get("phone", ""),
                    appt.get("status", ""),
                    appt.get("reason", "") or "",
                    appt.get("id_card", "") or appt.get("patient_record_id_card", "") or "",
                ),
            )

    def export_excel(self):
        def worker():
            try:
                from backend.integrations import export_appointments_xlsx
                self.latest_export_path = export_appointments_xlsx()
                self._log(f"Excel exported: {self.latest_export_path}")
                self._info("Export Complete", f"Excel exported:\n{self.latest_export_path}")
            except Exception as exc:
                self._error("Export Failed", str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def open_latest_export(self):
        path = self.latest_export_path
        if not path:
            export_dir = Path(self._collect_settings().get("EXPORT_DIR", "./exports"))
            if not export_dir.is_absolute():
                export_dir = BASE_DIR / export_dir
            path = str(export_dir)
        if os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showwarning("Open Export", "No export file or folder was found yet.")

    def sync_google_sheet(self):
        values = self._collect_settings()
        sheet_url = values.get("GOOGLE_SHEET_URL", "")
        credentials = values.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        if not sheet_url or not credentials:
            messagebox.showwarning("Google Sheet", "Please enter the Google Sheet URL and service account JSON path.")
            return

        def worker():
            try:
                from backend.integrations import sync_appointments_to_google_sheet
                result = sync_appointments_to_google_sheet(sheet_url, credentials)
                self._log(f"Google Sheet synced: {result}")
                self._info("Google Sheet", f"Synced {result['rows_synced']} appointments.")
            except Exception as exc:
                self._error("Google Sheet Sync Failed", str(exc))

        threading.Thread(target=worker, daemon=True).start()

    # ── API helper (uses stdlib only, no extra dependency) ───────────
    def _api_get(self, path: str):
        values = self._collect_settings()
        port = values.get("FASTAPI_PORT", "8000")
        url = f"http://127.0.0.1:{port}{path}"
        with urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode())

    def _api_post(self, path: str, body: dict | None = None):
        values = self._collect_settings()
        port = values.get("FASTAPI_PORT", "8000")
        url = f"http://127.0.0.1:{port}{path}"
        data = json.dumps(body or {}).encode()
        req = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())

    # ── Tab-changed auto-refresh ────────────────────────────────────
    def _on_tab_changed(self, event):
        tab = self.notebook.tab(self.notebook.select(), "text")
        if tab == "Today":
            self._load_today()
        elif tab == "No-Shows":
            self._load_noshows()
        elif tab == "Appointments":
            self.load_appointments()

    # ── Today's Appointments tab ────────────────────────────────────
    def _build_today_tab(self):
        bar = ttk.Frame(self.today_tab)
        bar.pack(fill="x", padx=10, pady=5)
        ttk.Button(bar, text="Refresh", command=self._load_today).pack(side="left", padx=5)
        ttk.Button(bar, text="Check In Selected", command=self._checkin_selected).pack(side="left", padx=5)

        cols = ("Time", "Patient", "Phone", "Reason", "Status")
        self.today_tree = ttk.Treeview(self.today_tab, columns=cols, show="headings")
        for c in cols:
            self.today_tree.heading(c, text=c)
            self.today_tree.column(c, width=150, anchor="w")
        self.today_tree.pack(fill="both", expand=True, padx=10, pady=10)

    def _load_today(self):
        def worker():
            try:
                data = self._api_get("/appointments/today")
                self.after(0, lambda d=data: self._fill_today(d))
            except Exception as exc:
                self._error("Today", f"Backend may not be running.\n{exc}")
        threading.Thread(target=worker, daemon=True).start()

    def _fill_today(self, data: list):
        for row in self.today_tree.get_children():
            self.today_tree.delete(row)
        for a in data:
            iid = str(a.get("id", ""))
            self.today_tree.insert("", "end", iid=iid, values=(
                a.get("time", ""), a.get("patient_name", ""),
                a.get("phone", ""), a.get("reason", "") or "-",
                a.get("status", ""),
            ))

    def _checkin_selected(self):
        selected = self.today_tree.selection()
        if not selected:
            self._warning("Check In", "Select an appointment first.")
            return
        def worker():
            for iid in selected:
                try:
                    self._api_post(f"/appointments/{iid}/checkin")
                except Exception as exc:
                    self._error("Check In", str(exc))
            self._load_today()
        threading.Thread(target=worker, daemon=True).start()

    # ── No-Show Stats tab ───────────────────────────────────────────
    def _build_noshow_tab(self):
        self.noshow_lbl = ttk.Label(self.noshow_tab, text="Click the tab to load stats.", font=("Segoe UI", 11))
        self.noshow_lbl.pack(pady=10)

        cols = ("Date", "Time", "Patient", "Phone", "Response")
        self.noshow_tree = ttk.Treeview(self.noshow_tab, columns=cols, show="headings")
        for c in cols:
            self.noshow_tree.heading(c, text=c)
            self.noshow_tree.column(c, width=150, anchor="w")
        self.noshow_tree.pack(fill="both", expand=True, padx=10, pady=10)

    def _load_noshows(self):
        def worker():
            try:
                data = self._api_get("/stats/no-shows")
                self.after(0, lambda d=data: self._fill_noshows(d))
            except Exception as exc:
                self._error("No-Shows", f"Backend may not be running.\n{exc}")
        threading.Thread(target=worker, daemon=True).start()

    def _fill_noshows(self, data: dict):
        total = data.get("total_appointments", 0)
        ns = data.get("total_no_shows", 0)
        rate = data.get("no_show_rate", 0)
        self.noshow_lbl.config(text=f"Total: {total}  |  No-Shows: {ns}  |  Rate: {rate}%")
        for row in self.noshow_tree.get_children():
            self.noshow_tree.delete(row)
        for a in data.get("recent_no_shows", []):
            self.noshow_tree.insert("", "end", values=(
                a.get("date", ""), a.get("time", ""),
                a.get("patient_name", ""), a.get("phone", ""),
                a.get("followup_response", "No response"),
            ))

    # ── Block Slots tab ─────────────────────────────────────────────
    def _build_block_tab(self):
        frame = ttk.LabelFrame(self.block_tab, text="Block a Slot or Full Day")
        frame.pack(fill="x", padx=10, pady=10)

        ttk.Label(frame, text="Date (YYYY-MM-DD):").grid(row=0, column=0, padx=6, pady=5, sticky="w")
        self.block_date = ttk.Entry(frame, width=20)
        self.block_date.grid(row=0, column=1, padx=6, pady=5)

        ttk.Label(frame, text="Time (leave empty for full day):").grid(row=1, column=0, padx=6, pady=5, sticky="w")
        self.block_time = ttk.Entry(frame, width=20)
        self.block_time.grid(row=1, column=1, padx=6, pady=5)

        ttk.Label(frame, text="Reason:").grid(row=2, column=0, padx=6, pady=5, sticky="w")
        self.block_reason = ttk.Entry(frame, width=40)
        self.block_reason.grid(row=2, column=1, padx=6, pady=5)

        ttk.Button(frame, text="Block Slot", command=self._do_block_slot).grid(row=3, column=0, columnspan=2, pady=10)

    def _do_block_slot(self):
        date_str = self.block_date.get().strip()
        if not date_str:
            self._warning("Block Slot", "Date is required.")
            return
        time_str = self.block_time.get().strip() or None
        reason = self.block_reason.get().strip()

        def worker():
            try:
                self._api_post("/slots/block", {"date": date_str, "time": time_str, "reason": reason})
                self._info("Block Slot", "Slot blocked successfully!")
                self.after(0, lambda: (self.block_date.delete(0, "end"), self.block_time.delete(0, "end"), self.block_reason.delete(0, "end")))
            except Exception as exc:
                self._error("Block Slot", str(exc))
        threading.Thread(target=worker, daemon=True).start()


    def on_close(self):
        backend_running = self.backend_proc and self.backend_proc.poll() is None
        bot_running = self.bot_proc and self.bot_proc.poll() is None
        if backend_running or bot_running:
            if messagebox.askyesno("Exit", "Stop running services before closing?"):
                self.stop_all()
        self.destroy()


def main():
    app = ClinicControlApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
