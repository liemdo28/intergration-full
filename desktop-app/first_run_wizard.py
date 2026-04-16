"""
ToastPOSManager — First-Run Setup Wizard

A modal wizard that greets the operator on first run.
Asks only business-operational questions — no raw JSON exposed.
Writes config automatically based on answers.

Steps:
  1. Welcome + store selection
  2. QuickBooks setup (optional)
  3. Google Drive setup (optional)
  4. Toast machine role (optional)
  5. Review + apply
"""

from __future__ import annotations

import json, logging, sys, traceback
from pathlib import Path
from tkinter import messagebox

try:
    import customtkinter as ctk
    CTK_AVAILABLE = True
except ImportError:
    CTK_AVAILABLE = False


# ---------------------------------------------------------------------------
# Path helpers (same pattern as app_paths)
# ---------------------------------------------------------------------------
def _resolve_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent

BUNDLE_DIR = _resolve_bundle_dir()
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLE_DIR


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def _load_local_config() -> dict:
    cfg_path = RUNTIME_DIR / "local-config.json"
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_local_config(data: dict) -> None:
    cfg_path = RUNTIME_DIR / "local-config.json"
    cfg_path.write_text(json.dumps(data, indent=4), encoding="utf-8")


def _load_env_file() -> dict:
    env_path = RUNTIME_DIR / ".env.qb"
    env = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _save_env_file(env: dict) -> None:
    env_path = RUNTIME_DIR / ".env.qb"
    lines = [f"{k}={v}" for k, v in sorted(env.items())]
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Known store options
# ---------------------------------------------------------------------------
KNOWN_STORES = [
    "Stockton", "The Rim", "Stone Oak", "Bandera", "Copper",
    "Jinya", "WA3", "IFT",
]

# ---------------------------------------------------------------------------
# Wizard window
# ---------------------------------------------------------------------------

class FirstRunWizard(ctk.CTk):
    """Modal first-run wizard window."""

    WINDOW_W = 620
    WINDOW_H = 520

    def __init__(self):
        super().__init__()
        self.title("ToastPOSManager — Setup Wizard")
        self.geometry(f"{self.WINDOW_W}x{self.WINDOW_H}")
        self.resizable(False, False)
        ctk.set_appearance_mode("dark")

        # State
        self.step = 0
        self.total_steps = 5
        self.selected_stores: list[str] = []
        self.qb_file_path: str = ""
        self.qb_wanted: bool = False
        self.drive_wanted: bool = False
        self.machine_role: str = "both"  # "download" | "qb_sync" | "both"

        # Config we will build
        self.local_config = _load_local_config()
        self.env_values = _load_env_file()

        # Layout
        self.content_frame = None
        self.nav_frame = None
        self._build_ui()

    def _build_ui(self) -> None:
        # Header bar
        header = ctk.CTkFrame(self, fg_color="#1e293b", height=48)
        header.pack(fill="x", padx=0, pady=0)
        header.pack_propagate(False)
        ctk.CTkLabel(
            header, text="ToastPOSManager Setup",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#f1f5f9",
        ).pack(side="left", padx=16, pady=10)
        ctk.CTkLabel(
            header, text=f"Step {self.step + 1} of {self.total_steps}",
            text_color="#64748b",
        ).pack(side="right", padx=16, pady=10)

        # Progress bar
        self.progress = ctk.CTkProgressBar(self, height=4, progress_color="#22c55e")
        self.progress.pack(fill="x", padx=0, pady=0)
        self.progress.set(0)

        # Content area
        if self.content_frame:
            self.content_frame.destroy()
        self.content_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.content_frame.pack(fill="both", expand=True, padx=40, pady=20)

        # Nav buttons
        if self.nav_frame:
            self.nav_frame.destroy()
        self.nav_frame = ctk.CTkFrame(self, fg_color="#0f172a", height=60)
        self.nav_frame.pack(fill="x", side="bottom", pady=0)
        self.nav_frame.pack_propagate(False)

        self._render_step()

    def _render_step(self) -> None:
        self.progress.set(self.step / self.total_steps)

        for widget in self.content_frame.winfo_children():
            widget.destroy()

        if self.step == 0:
            self._step_welcome()
        elif self.step == 1:
            self._step_stores()
        elif self.step == 2:
            self._step_quickbooks()
        elif self.step == 3:
            self._step_drive()
        elif self.step == 4:
            self._step_review()

        self._render_nav()

    def _step_welcome(self) -> None:
        ctk.CTkLabel(
            self.content_frame, text="Welcome to ToastPOSManager",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color="#f1f5f9",
        ).pack(anchor="w", pady=(20, 8))
        ctk.CTkLabel(
            self.content_frame,
            text="This wizard will help you configure the app for your operation.\n"
                 "You can change all settings later in Settings & Recovery.",
            text_color="#94a3b8", font=ctk.CTkFont(size=13),
            wraplength=520,
        ).pack(anchor="w", pady=(0, 24))

        # Feature overview cards
        features = [
            ("Download Reports", "Automate Toast report downloads via browser", "#22c55e"),
            ("QB Sync", "Sync sales data to QuickBooks Desktop", "#3b82f6"),
            ("Drive Upload", "Upload reports to Google Drive", "#eab308"),
            ("Remove Transactions", "Clean up QB company files", "#ef4444"),
        ]
        for feat, desc, color in features:
            card = ctk.CTkFrame(self.content_frame, fg_color="#1e293b", corner_radius=8)
            card.pack(fill="x", pady=4)
            dot = ctk.CTkLabel(card, text="●", text_color=color, font=ctk.CTkFont(size=14))
            dot.pack(side="left", padx=(12, 8), pady=10)
            ctk.CTkLabel(card, text=feat, font=ctk.CTkFont(size=13, weight="bold"), text_color="#f1f5f9").pack(side="left", pady=10)
            ctk.CTkLabel(card, text=desc, text_color="#64748b", font=ctk.CTkFont(size=11)).pack(side="right", padx=12, pady=10)

    def _step_stores(self) -> None:
        ctk.CTkLabel(
            self.content_frame, text="Select Your Stores",
            font=ctk.CTkFont(size=20, weight="bold"), text_color="#f1f5f9",
        ).pack(anchor="w", pady=(20, 6))
        ctk.CTkLabel(
            self.content_frame,
            text="Check the stores you operate. The app will tailor its behavior for each.",
            text_color="#94a3b8", font=ctk.CTkFont(size=12),
        ).pack(anchor="w", pady=(0, 16))

        store_vars = {}
        for store in KNOWN_STORES:
            var = ctk.BooleanVar(value=store in self.selected_stores)
            store_vars[store] = var
            cb = ctk.CTkCheckBox(
                self.content_frame, text=store, variable=var,
                onvalue=True, offvalue=False,
            )
            cb.pack(anchor="w", pady=2)

        self._store_vars = store_vars  # store for later retrieval

        ctk.CTkLabel(
            self.content_frame,
            text="Don't see your store? You can add it later in Settings.",
            text_color="#475569", font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(12, 0))

    def _step_quickbooks(self) -> None:
        ctk.CTkLabel(
            self.content_frame, text="QuickBooks Setup",
            font=ctk.CTkFont(size=20, weight="bold"), text_color="#f1f5f9",
        ).pack(anchor="w", pady=(20, 6))
        ctk.CTkLabel(
            self.content_frame,
            text="QuickBooks Desktop must be installed on this machine to use QB Sync.",
            text_color="#94a3b8", font=ctk.CTkFont(size=12), wraplength=520,
        ).pack(anchor="w", pady=(0, 16))

        self.qb_wanted_var = ctk.BooleanVar(value=self.qb_wanted)
        ctk.CTkCheckBox(
            self.content_frame, text="I want to use QB Sync on this machine",
            variable=self.qb_wanted_var, onvalue=True, offvalue=False,
        ).pack(anchor="w", pady=8)

        qb_hint = ctk.CTkLabel(
            self.content_frame,
            text="Your QB company file (.qbw) path will be configured in Settings → QB Sync options after setup.",
            text_color="#475569", font=ctk.CTkFont(size=11), wraplength=500,
        )
        qb_hint.pack(anchor="w", pady=(4, 0))

    def _step_drive(self) -> None:
        ctk.CTkLabel(
            self.content_frame, text="Google Drive Setup",
            font=ctk.CTkFont(size=20, weight="bold"), text_color="#f1f5f9",
        ).pack(anchor="w", pady=(20, 6))
        ctk.CTkLabel(
            self.content_frame,
            text="Connect Google Drive to upload reports automatically and enable Drive Inventory scanning.",
            text_color="#94a3b8", font=ctk.CTkFont(size=12), wraplength=520,
        ).pack(anchor="w", pady=(0, 16))

        self.drive_wanted_var = ctk.BooleanVar(value=self.drive_wanted)
        ctk.CTkCheckBox(
            self.content_frame, text="I want to use Google Drive features",
            variable=self.drive_wanted_var, onvalue=True, offvalue=False,
        ).pack(anchor="w", pady=8)

        drive_hint = ctk.CTkLabel(
            self.content_frame,
            text="You will need a credentials.json file from Google Cloud Console. "
                 "Place it in the app folder and click 'Connect Google Drive' in Settings.",
            text_color="#475569", font=ctk.CTkFont(size=11), wraplength=500,
        )
        drive_hint.pack(anchor="w", pady=(4, 0))

    def _step_review(self) -> None:
        ctk.CTkLabel(
            self.content_frame, text="Review & Apply",
            font=ctk.CTkFont(size=20, weight="bold"), text_color="#f1f5f9",
        ).pack(anchor="w", pady=(20, 6))

        # Summary of what will be saved
        stores = list(self._store_vars.keys()) if hasattr(self, "_store_vars") else self.selected_stores
        selected = [s for s in stores if (self._store_vars.get(s) or ctk.BooleanVar()).get()]
        qb = self.qb_wanted_var.get() if hasattr(self, "qb_wanted_var") else self.qb_wanted
        drive = self.drive_wanted_var.get() if hasattr(self, "drive_wanted_var") else self.drive_wanted

        rows = [
            ("Stores selected", ", ".join(selected) if selected else "(none)"),
            ("QB Sync", "Yes — configure .qbw path in Settings" if qb else "No"),
            ("Google Drive", "Yes — connect in Settings" if drive else "No"),
            ("Machine role", self.machine_role),
        ]
        for label, value in rows:
            row = ctk.CTkFrame(self.content_frame, fg_color="#1e293b", corner_radius=6)
            row.pack(fill="x", pady=4)
            ctk.CTkLabel(row, text=label, text_color="#64748b", font=ctk.CTkFont(size=11), width=160, anchor="w").pack(side="left", padx=(12, 8), pady=8)
            ctk.CTkLabel(row, text=value, text_color="#f1f5f9", font=ctk.CTkFont(size=12), anchor="w").pack(side="left", pady=8, padx=(0, 12))

        note = ctk.CTkLabel(
            self.content_frame,
            text="All settings can be changed later in Settings & Recovery. "
                 "Your local-config.json and .env.qb will be updated.",
            text_color="#475569", font=ctk.CTkFont(size=11), wraplength=500,
        )
        note.pack(anchor="w", pady=(16, 0))

    def _render_nav(self) -> None:
        for widget in self.nav_frame.winfo_children():
            widget.destroy()

        btn_frame = ctk.CTkFrame(self.nav_frame, fg_color="transparent")
        btn_frame.pack(fill="x", padx=20, pady=10)

        if self.step > 0:
            ctk.CTkButton(
                btn_frame, text="← Back", width=100,
                command=lambda: self._nav(-1),
            ).pack(side="left")

        if self.step < self.total_steps - 1:
            ctk.CTkButton(
                btn_frame, text="Next →", width=120, fg_color="#22c55e",
                hover_color="#16a34a",
                command=lambda: self._nav(1),
            ).pack(side="right")
        else:
            ctk.CTkButton(
                btn_frame, text="Save & Launch", width=140, fg_color="#22c55e",
                hover_color="#16a34a",
                command=self._apply_and_close,
            ).pack(side="right")

        ctk.CTkButton(
            btn_frame, text="Skip Setup", width=100,
            fg_color="transparent", text_color="#64748b",
            hover_color="transparent",
            command=self._skip_and_close,
        ).pack(side="right", padx=(0, 16))

    def _nav(self, direction: int) -> None:
        if direction == 1 and self.step == 1:
            # Capture store selection before advancing
            self.selected_stores = [
                store for store, var in self._store_vars.items()
                if var.get()
            ]
        self.step = max(0, min(self.total_steps - 1, self.step + direction))
        self._render_step()

    def _apply_and_close(self) -> None:
        try:
            # Capture final selections
            self.selected_stores = [
                store for store, var in self._store_vars.items()
                if var.get()
            ]
            self.qb_wanted = self.qb_wanted_var.get()
            self.drive_wanted = self.drive_wanted_var.get()

            # Write local-config.json
            cfg = _load_local_config()
            if self.selected_stores:
                if "enabled_stores" not in cfg:
                    cfg["enabled_stores"] = self.selected_stores

            _save_local_config(cfg)
            logging.info(f"Setup wizard saved config for {len(self.selected_stores)} stores")

            messagebox.showinfo(
                "Setup Complete",
                "Configuration saved. The main app will now open.\n"
                "You can change all settings in Settings & Recovery.",
            )
            self.destroy()

        except Exception as exc:
            logging.error(f"Setup wizard apply failed: {exc}", exc_info=True)
            messagebox.showwarning(
                "Setup Error",
                f"Could not save configuration:\n\n{exc}\n\nThe app will continue without saving.",
            )
            self.destroy()

    def _skip_and_close(self) -> None:
        logging.info("Setup wizard skipped by user")
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point (called by launcher.py)
# ---------------------------------------------------------------------------

def run() -> None:
    """Run the wizard as a blocking modal window."""
    if not CTK_AVAILABLE:
        print("first_run_wizard requires customtkinter (not available in dev without deps)")
        return

    logging.basicConfig(level=logging.INFO)
    logging.info("Starting first-run wizard")

    try:
        app = FirstRunWizard()
        app.mainloop()
    except Exception as exc:
        logging.error(f"Wizard crashed: {exc}", exc_info=True)
        # Don't block — the launcher will proceed to the app anyway


if __name__ == "__main__":
    run()
