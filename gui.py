"""
Remote Voice — Configuration GUI
Manage settings and start/stop the server from one window.
"""

import json
import re
import socket
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

CONFIG_PATH = Path(__file__).parent / "config.json"
SERVER_SCRIPT = Path(__file__).parent / "server.py"

VOICE_MODELS = [
    "nemo-parakeet-tdt-0.6b-v2",
    "nemo-parakeet-tdt-0.6b-v3",
    "nemo-parakeet-ctc-0.6b",
    "nemo-parakeet-rnnt-0.6b",
    "nemo-canary-1b-v2",
    "nemo-conformer-ctc",
    "nemo-conformer-rnnt",
    "nemo-conformer-tdt",
    "nemo-conformer-aed",
    "whisper",
    "whisper-ort",
]

DEFAULTS = {
    "server_port": 8787,
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen2.5:3b",
    "voice_model": "nemo-parakeet-tdt-0.6b-v2",
    "cleanup_prompt": "You clean transcripts that have already been partially processed.\nNumbers, punctuation symbols, and filler words (um, uh) have already been handled.\n\nYour remaining tasks:\n\n1. Self-corrections: if the speaker corrects themselves (e.g., \"sorry\",\n   \"I meant\", \"no wait\", \"actually\"), delete the wrong part and the\n   correction word, keep only the fix. But if these words are used\n   naturally (e.g., \"I'm sorry for the delay\"), keep the sentence intact.\n\n2. Remove filler \"like\" ONLY when used as a filler. Keep \"like\" meaning\n   enjoy or similar.\n\n3. Smooth awkward phrasing left after filler removal.\n\n4. If the speaker restates something (says the same thing differently),\n   keep only the final version.\n\n5. Do NOT translate. Do NOT delete meaningful words.\n\nOutput ONLY the cleaned text.",
}


class RemoteVoiceGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Remote Voice Server")
        self.root.geometry("700x750")
        self.root.minsize(600, 600)

        self.server_process = None
        self.config = self.load_config()
        self.ollama_models = self.fetch_ollama_models()

        self.build_ui()
        self.populate_fields()

    def fetch_ollama_models(self) -> list[str]:
        """Query Ollama API for installed models."""
        url = self.config.get("ollama_url", "http://localhost:11434")
        try:
            with urlopen(f"{url}/api/tags", timeout=3) as resp:
                data = json.loads(resp.read())
                return [m["name"] for m in data.get("models", [])]
        except Exception:
            return ["qwen2.5:3b", "qwen2.5:7b"]

    def load_config(self) -> dict:
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                return {**DEFAULTS, **json.load(f)}
        return dict(DEFAULTS)

    def save_config(self):
        self.config["server_port"] = int(self.port_var.get())
        self.config["ollama_url"] = self.ollama_url_var.get()
        self.config["ollama_model"] = self.ollama_model_var.get()
        self.config["voice_model"] = self.voice_model_var.get()
        self.config["cleanup_prompt"] = self.prompt_text.get("1.0", tk.END).rstrip("\n")

        with open(CONFIG_PATH, "w") as f:
            json.dump(self.config, f, indent=4)

    def build_ui(self):
        # --- Server controls ---
        ctrl_frame = ttk.LabelFrame(self.root, text="Server", padding=10)
        ctrl_frame.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.status_label = ttk.Label(ctrl_frame, text="Stopped", foreground="red", font=("", 11, "bold"))
        self.status_label.pack(side=tk.LEFT, padx=(0, 15))

        self.stop_btn = ttk.Button(ctrl_frame, text="Stop Server", command=self.stop_server, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT, padx=5)

        self.start_btn = ttk.Button(ctrl_frame, text="Start Server", command=self.start_server)
        self.start_btn.pack(side=tk.RIGHT, padx=5)

        self.save_btn = ttk.Button(ctrl_frame, text="Save Config", command=self.on_save)
        self.save_btn.pack(side=tk.RIGHT, padx=5)

        # --- Settings ---
        settings_frame = ttk.LabelFrame(self.root, text="Settings", padding=10)
        settings_frame.pack(fill=tk.X, padx=10, pady=5)

        row = 0
        ttk.Label(settings_frame, text="Server Port:").grid(row=row, column=0, sticky=tk.W, pady=3)
        self.port_var = tk.StringVar()
        ttk.Entry(settings_frame, textvariable=self.port_var, width=10).grid(row=row, column=1, sticky=tk.W, pady=3)

        row += 1
        ttk.Label(settings_frame, text="Ollama URL:").grid(row=row, column=0, sticky=tk.W, pady=3)
        self.ollama_url_var = tk.StringVar()
        ttk.Entry(settings_frame, textvariable=self.ollama_url_var, width=40).grid(row=row, column=1, sticky=tk.W, pady=3)

        row += 1
        ttk.Label(settings_frame, text="LLM Model:").grid(row=row, column=0, sticky=tk.W, pady=3)
        self.ollama_model_var = tk.StringVar()
        self.ollama_combo = ttk.Combobox(settings_frame, textvariable=self.ollama_model_var, width=30, values=self.ollama_models)
        self.ollama_combo.grid(row=row, column=1, sticky=tk.W, pady=3)
        refresh_btn = ttk.Button(settings_frame, text="Refresh", width=8, command=self.refresh_ollama_models)
        refresh_btn.grid(row=row, column=2, padx=(5, 0), pady=3)

        row += 1
        ttk.Label(settings_frame, text="Voice Model:").grid(row=row, column=0, sticky=tk.W, pady=3)
        self.voice_model_var = tk.StringVar()
        ttk.Combobox(settings_frame, textvariable=self.voice_model_var, width=40, values=VOICE_MODELS).grid(row=row, column=1, sticky=tk.W, pady=3)

        settings_frame.columnconfigure(1, weight=1)

        # --- Prompt ---
        prompt_frame = ttk.LabelFrame(self.root, text="Cleanup Prompt (sent to Ollama)", padding=10)
        prompt_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.prompt_text = scrolledtext.ScrolledText(prompt_frame, wrap=tk.WORD, height=10, font=("Consolas", 10))
        self.prompt_text.pack(fill=tk.BOTH, expand=True)

        # --- Logs ---
        log_frame = ttk.LabelFrame(self.root, text="Server Logs", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))

        log_buttons = ttk.Frame(log_frame)
        log_buttons.pack(fill=tk.X, pady=(0, 5))
        ttk.Button(log_buttons, text="Copy Logs", command=self.copy_logs).pack(side=tk.LEFT)
        ttk.Button(log_buttons, text="Clear Logs", command=self.clear_logs).pack(side=tk.LEFT, padx=5)

        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=8, font=("Consolas", 9),
                                                   state=tk.DISABLED, background="#1e1e1e", foreground="#cccccc")
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def populate_fields(self):
        self.port_var.set(str(self.config["server_port"]))
        self.ollama_url_var.set(self.config["ollama_url"])
        self.ollama_model_var.set(self.config["ollama_model"])
        self.voice_model_var.set(self.config["voice_model"])
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", self.config["cleanup_prompt"].rstrip("\n"))

    def copy_logs(self):
        logs = self.log_text.get("1.0", tk.END).strip()
        self.root.clipboard_clear()
        self.root.clipboard_append(logs)
        self.append_log("Logs copied to clipboard.\n")

    def clear_logs(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def refresh_ollama_models(self):
        self.ollama_models = self.fetch_ollama_models()
        self.ollama_combo.configure(values=self.ollama_models)
        self.append_log(f"Found {len(self.ollama_models)} Ollama models: {', '.join(self.ollama_models)}\n")

    def on_save(self):
        self.save_config()
        self.append_log("Config saved. Restart the server for changes to take effect.\n")

    @staticmethod
    def strip_ansi(text: str) -> str:
        text = text.replace("\x00", "")              # null bytes from wide-char output
        text = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", text)  # standard ANSI sequences
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)  # remaining control chars
        return text

    LOG_MAX_LINES = 2000

    def append_log(self, text):
        text = self.strip_ansi(text)
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text)
        # Auto-trim: when log exceeds limit, drop the oldest half
        total_lines = int(self.log_text.index("end-1c").split(".")[0])
        if total_lines > self.LOG_MAX_LINES:
            half = total_lines // 2
            self.log_text.delete("1.0", f"{half}.0")
            self.log_text.insert("1.0", f"--- older logs trimmed ({half} lines) ---\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def is_port_in_use(self, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex(("127.0.0.1", port)) == 0

    def start_server(self):
        if self.server_process and self.server_process.poll() is None:
            return

        port = int(self.port_var.get())
        if self.is_port_in_use(port):
            self.append_log(f"ERROR: Port {port} is already in use. Another server instance may be running.\n")
            messagebox.showerror("Server Already Running", f"Port {port} is already in use.\n\nAnother server instance (or start.bat) may be running. Stop it first.")
            return

        self.save_config()
        self.append_log("Starting server...\n")

        self.server_process = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            cwd=str(SERVER_SCRIPT.parent),
        )

        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.status_label.configure(text="Running", foreground="green")

        thread = threading.Thread(target=self.read_server_output, daemon=True)
        thread.start()

    def read_server_output(self):
        try:
            for line in self.server_process.stdout:
                self.root.after(0, self.append_log, line)
        except Exception:
            pass
        finally:
            self.root.after(0, self.on_server_stopped)

    def on_server_stopped(self):
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        self.status_label.configure(text="Stopped", foreground="red")
        self.append_log("Server stopped.\n")

    def stop_server(self):
        if self.server_process and self.server_process.poll() is None:
            self.append_log("Stopping server...\n")
            self.server_process.terminate()
            self.server_process.wait(timeout=10)

    def on_close(self):
        if self.server_process and self.server_process.poll() is None:
            self.stop_server()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = RemoteVoiceGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
