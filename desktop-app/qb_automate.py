"""
QB Automate: Launch QB Desktop, login with password, dismiss popups.
Unified version combining download-report and remove-transaction automation.
"""

import os
import sys
import time
import subprocess
from pathlib import Path
from app_paths import runtime_path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ── Load .env ────────────────────────────────────────────────────────
def load_env(filepath):
    if not Path(filepath).exists():
        return
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key not in os.environ:
                os.environ[key] = val


load_env(runtime_path(".env.qb"))

QB_EXE = os.environ.get(
    "QB_EXE_PATH",
    r"C:\Program Files\Intuit\QuickBooks Enterprise Solutions 24.0\QBWEnterprise.exe",
)
QB_COMPANY_DIR = os.environ.get("QB_COMPANY_DIR", r"D:\QB")

PASSWORDS = {
    "pass1": os.environ.get("QB_PASSWORD1", ""),
    "pass2": os.environ.get("QB_PASSWORD2", ""),
    "pass3": os.environ.get("QB_PASSWORD3", ""),
}

SAFE_QB_APP_PROCESS_NAMES = {
    "QBWEnterprise.exe",
    "QBW32Enterprise.exe",
    "QBW.EXE",
}

KNOWN_QB_POPUP_RULES = [
    {
        "title_patterns": ["memorized transactions"],
        "button_titles": ["Enter All Later", "Enter All  Later", "Later", "Close"],
        "label": "Memorized Transactions",
        "allow_escape": True,
    },
    {
        "title_patterns": ["update", "quickbooks update", "intuit update"],
        "button_titles": ["Remind me later", "Remind Me Later", "Skip", "Not Now", "Close"],
        "label": "Update prompt",
        "allow_escape": True,
    },
    {
        "title_patterns": ["backup", "restore", "scheduled backup"],
        "button_titles": ["Cancel", "Close", "No", "Not Now"],
        "label": "Backup/restore prompt",
        "allow_escape": True,
    },
    {
        "title_patterns": ["missing checks", "payments to deposit", "accountant changes"],
        "button_titles": ["Close", "Cancel", "No"],
        "label": "Accounting reminder",
        "allow_escape": False,
    },
]


def _normalize_text(value):
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def company_file_matches(path, expected_match):
    if not expected_match:
        return True
    filename = Path(path).stem
    return _normalize_text(expected_match) in _normalize_text(filename)


def validate_company_file_path(path, expected_match=None, store_name=None):
    file_path = Path(path)
    if not file_path.exists():
        return False, f"QB company file not found: {file_path}"
    if expected_match and not company_file_matches(file_path, expected_match):
        label = store_name or "store"
        return False, (
            f"QB company guard failed for {label}. Expected file name to contain "
            f"'{expected_match}', got '{file_path.name}'."
        )
    return True, f"QB company file preflight passed: {file_path.name}"


def _candidate_qb_paths():
    env_path = os.environ.get("QB_EXE_PATH")
    if env_path:
        yield Path(env_path)

    program_roots = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
    ]
    editions = [
        "QuickBooks Enterprise Solutions",
        "QuickBooks Desktop Enterprise",
        "QuickBooks Enterprise Accountant",
    ]
    exe_names = ["QBWEnterprise.exe", "QBW32Enterprise.exe", "QBW.exe"]

    for root in program_roots:
        for version in range(30, 19, -1):
            for edition in editions:
                base = root / "Intuit" / f"{edition} {version}.0"
                for exe_name in exe_names:
                    yield base / exe_name


def resolve_qb_executable():
    for candidate in _candidate_qb_paths():
        if candidate.exists():
            return candidate
    return None


def log(msg):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")


def _emit(callback, msg):
    if callback:
        callback(msg)
    else:
        log(msg)


def _is_safe_popup_title(title):
    normalized = str(title or "").strip().lower()
    if not normalized or normalized == "workspace":
        return False
    if "intuit quickbooks" in normalized:
        return False
    return any(
        pattern in normalized
        for rule in KNOWN_QB_POPUP_RULES
        for pattern in rule["title_patterns"]
    )


def _matching_popup_rule(title):
    normalized = str(title or "").strip().lower()
    for rule in KNOWN_QB_POPUP_RULES:
        if any(pattern in normalized for pattern in rule["title_patterns"]):
            return rule
    return None


# ── Close QB ─────────────────────────────────────────────────────────
def close_qb_completely(callback=None, *, force_kill=False, kill_timeout=8):
    """Terminate QuickBooks app processes and only force-kill survivors when explicitly allowed."""
    import psutil
    _emit(callback, "Closing QuickBooks...")

    touched = []
    for proc_name in SAFE_QB_APP_PROCESS_NAMES:
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() == proc_name.lower():
                    proc.terminate()
                    touched.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    if touched:
        _, alive = psutil.wait_procs(touched, timeout=max(1, int(kill_timeout)))
        if alive and force_kill:
            for proc in alive:
                try:
                    proc.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            time.sleep(2)
            msg = "QB closed after force-killing stuck QuickBooks process(es)"
        elif alive:
            survivor_names = ", ".join(sorted({proc.name() for proc in alive if proc.is_running()})) or "QuickBooks"
            msg = (
                f"QuickBooks did not close cleanly ({survivor_names}). "
                "Please close it manually, then retry. Force-kill is disabled by default for safety."
            )
        else:
            time.sleep(2)
            msg = "QB closed"
    else:
        msg = "QB was not running"

    _emit(callback, msg)
    return "did not close cleanly" not in msg


# ── Open QB with file ────────────────────────────────────────────────
def open_qb_with_file(qbw_path, password_key="pass1", callback=None, expected_match=None, store_name=None):
    """
    Open QB Desktop with a specific company file, login, dismiss popups.

    Args:
        qbw_path: Full path to .qbw file
        password_key: Key in PASSWORDS dict (pass1/pass2/pass3)
        callback: Optional callback(msg) for progress updates

    Returns:
        True if QB is open and ready, False otherwise
    """
    return _open_qb_company_file(
        qbw_path,
        password_key=password_key,
        callback=callback,
        expected_match=expected_match,
        store_name=store_name,
        opened_label=os.path.basename(qbw_path),
    )


# ── Open store (used by QB sync tab) ─────────────────────────────────
def open_store(store_name, store_paths, qbw_match=None, password_key="pass1"):
    """Open a store: launch QB with file path, login."""
    if store_name not in store_paths:
        log(f"No .qbw path for '{store_name}'")
        return False

    qbw_path = store_paths[store_name]
    log(f"Opening store: {store_name}")
    log(f"  File: {qbw_path}")
    return _open_qb_company_file(
        qbw_path,
        password_key=password_key,
        callback=log,
        expected_match=qbw_match,
        store_name=store_name,
        opened_label=store_name,
    )


def _open_qb_company_file(qbw_path, *, password_key="pass1", callback=None, expected_match=None, store_name=None, opened_label=None):
    from pywinauto import Application

    password = PASSWORDS.get(password_key, "")
    qb_exe = resolve_qb_executable()

    valid_file, file_msg = validate_company_file_path(qbw_path, expected_match, store_name)
    if not valid_file:
        _emit(callback, file_msg)
        return False
    if not qb_exe:
        _emit(callback, "QuickBooks executable not found. Check QB_EXE_PATH or install path.")
        return False

    _emit(callback, file_msg)
    _emit(callback, f"Opening QB with: {opened_label or os.path.basename(qbw_path)}")
    subprocess.Popen([str(qb_exe), qbw_path])
    time.sleep(10)

    app = None
    for attempt in range(15):
        for title_re in [".*QuickBooks Desktop Login.*", ".*QuickBooks.*", ".*Intuit.*"]:
            try:
                app = Application(backend="uia").connect(title_re=title_re, timeout=3)
                break
            except Exception:
                pass
        if app:
            break
        _emit(callback, f"  Waiting for QB... ({(attempt + 1) * 5}s)")
        time.sleep(5)

    if not app:
        _emit(callback, "Failed to connect to QB window")
        return False

    _emit(callback, "Connected to QB window")
    time.sleep(3)
    logged_in = _do_login(app, password, lambda msg: _emit(callback, msg))
    if not logged_in:
        _emit(callback, "Login failed")
        return False

    _wait_for_ready(app, lambda msg: _emit(callback, msg))
    if expected_match:
        _warn_if_window_title_mismatch(app, expected_match, lambda msg: _emit(callback, msg))
    _dismiss_all_popups(app, lambda msg: _emit(callback, msg))
    _emit(callback, "QB is ready!")
    return True


# ── Internal helpers ──────────────────────────────────────────────────
def _do_login(app, password, _log):
    """Enter password and click OK."""
    dlg = _find_login_dialog(app)
    if not dlg:
        _log("No login dialog found - may not need password")
        return True

    _log("Entering password...")

    try:
        pwd_field = dlg.child_window(auto_id="15924", control_type="Edit")
        if not pwd_field.exists(timeout=3):
            edits = dlg.descendants(control_type="Edit")
            if not edits:
                _log("Cannot find password field")
                return False
            pwd_field = edits[0]

        pwd_field.click_input()
        time.sleep(0.3)
        pwd_field.set_edit_text(password)
        time.sleep(0.5)

        ok_btn = dlg.child_window(title="OK", auto_id="51")
        ok_btn.click_input()
        _log("Clicked OK...")
        time.sleep(8)

        try:
            win = app.top_window()
            check = win.child_window(title="QuickBooks Desktop Login")
            if check.exists(timeout=5):
                _log("Wrong password - login dialog still showing")
                return False
        except Exception:
            pass

        _log("Login successful!")
        return True

    except Exception as e:
        _log(f"Login error: {e}")
        return False


def _find_login_dialog(app, timeout=30):
    """Find QB login dialog."""
    for _ in range(timeout // 3):
        try:
            win = app.top_window()
            dlg = win.child_window(title="QuickBooks Desktop Login")
            if dlg.exists(timeout=2):
                return dlg
        except Exception:
            pass
        try:
            dlg = app.window(title="QuickBooks Desktop Login")
            if dlg.exists(timeout=2):
                return dlg
        except Exception:
            pass
        time.sleep(3)
    return None


def _wait_for_ready(app, _log, timeout=90):
    """Wait for QB to finish loading."""
    _log("Waiting for QB to load...")
    for i in range(timeout // 3):
        try:
            win = app.top_window()
            title = win.window_text()
            if title and "Login" not in title and "No Company" not in title:
                _log(f"QB ready: {title}")
                return True
        except Exception:
            pass
        time.sleep(3)

    _log("QB load timeout, continuing anyway...")
    return True


def _warn_if_window_title_mismatch(app, expected_match, _log):
    try:
        title = app.top_window().window_text()
    except Exception:
        return
    if title and _normalize_text(expected_match) not in _normalize_text(title):
        _log(
            "Warning: QuickBooks window title does not appear to match the expected company. "
            "Verify the opened company file before continuing."
        )


def _dismiss_all_popups(app, _log):
    """Close only known low-risk popups after login and leave unknown dialogs alone."""
    import pywinauto.keyboard as kb
    import pywinauto.mouse as mouse

    _log("Dismissing popups...")
    closed = 0
    time.sleep(3)

    for attempt in range(10):
        found = False
        try:
            win = app.top_window()

            children = []
            try:
                children = win.children()
            except Exception:
                pass

            for child in children:
                try:
                    ctitle = child.window_text()
                    ctype = child.element_info.control_type
                except Exception:
                    continue

                if not ctitle or ctitle == "Workspace":
                    continue
                if ctype not in ("Window", "Dialog", "Pane"):
                    continue
                if "Intuit QuickBooks" in ctitle:
                    continue
                rule = _matching_popup_rule(ctitle)
                if not rule:
                    _log(f"  Leaving unknown popup open for review: '{ctitle}'")
                    continue

                clicked = False
                for btn_title in rule["button_titles"]:
                    try:
                        for ct in ["Button", "Pane"]:
                            try:
                                btn = child.child_window(title=btn_title, control_type=ct)
                                if btn.exists(timeout=0.5):
                                    btn.click_input()
                                    clicked = True
                                    break
                            except Exception:
                                pass
                        if clicked:
                            break
                        try:
                            btn = child.child_window(title=btn_title)
                            if btn.exists(timeout=0.5):
                                btn.click_input()
                                clicked = True
                                break
                        except Exception:
                            pass
                    except Exception:
                        pass

                if not clicked and rule.get("allow_escape"):
                    try:
                        rect = child.rectangle()
                        mouse.click(coords=(rect.right - 15, rect.top + 15))
                        clicked = True
                    except Exception:
                        pass

                if not clicked and rule.get("allow_escape"):
                    try:
                        kb.send_keys("{ESCAPE}")
                        clicked = True
                    except Exception:
                        pass

                if clicked:
                    _log(f"  Closed popup: {rule['label']} -> '{ctitle}'")
                    closed += 1
                    found = True
                    time.sleep(2)
                    break

            if not found:
                break

        except Exception:
            break

    _log(f"  Dismissed {closed} popup(s)")
    return closed
