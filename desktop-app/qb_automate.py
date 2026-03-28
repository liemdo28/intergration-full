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


# ── Close QB ─────────────────────────────────────────────────────────
def close_qb_completely(callback=None):
    """Terminate QB processes, then force-kill survivors only if needed."""
    import psutil
    if callback:
        callback("Closing QuickBooks...")
    else:
        log("Closing QuickBooks...")

    touched = []
    for proc_name in ["QBWEnterprise.exe", "QBW32Enterprise.exe", "QBW.EXE",
                       "qbupdate.exe", "QBCFMonitorService.exe"]:
        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() == proc_name.lower():
                    proc.terminate()
                    touched.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    if touched:
        gone, alive = psutil.wait_procs(touched, timeout=8)
        for proc in alive:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(2)
        msg = "QB closed"
    else:
        msg = "QB was not running"

    if callback:
        callback(msg)
    else:
        log(msg)


# ── Open QB with file ────────────────────────────────────────────────
def open_qb_with_file(qbw_path, password_key="pass1", callback=None):
    """
    Open QB Desktop with a specific company file, login, dismiss popups.

    Args:
        qbw_path: Full path to .qbw file
        password_key: Key in PASSWORDS dict (pass1/pass2/pass3)
        callback: Optional callback(msg) for progress updates

    Returns:
        True if QB is open and ready, False otherwise
    """
    from pywinauto import Application

    def _log(msg):
        if callback:
            callback(msg)
        else:
            log(msg)

    password = PASSWORDS.get(password_key, "")
    qb_exe = resolve_qb_executable()

    if not Path(qbw_path).exists():
        _log(f"QB company file not found: {qbw_path}")
        return False
    if not qb_exe:
        _log("QuickBooks executable not found. Check QB_EXE_PATH or install path.")
        return False

    _log(f"Opening QB with: {os.path.basename(qbw_path)}")

    # Launch QB with company file
    subprocess.Popen([str(qb_exe), qbw_path])
    time.sleep(10)

    # Connect to QB window
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
        _log(f"  Waiting for QB... ({(attempt + 1) * 5}s)")
        time.sleep(5)

    if not app:
        _log("Failed to connect to QB window")
        return False

    _log("Connected to QB window")

    # Login
    time.sleep(3)
    logged_in = _do_login(app, password, _log)
    if not logged_in:
        _log("Login failed")
        return False

    # Wait for QB to be ready
    _wait_for_ready(app, _log)

    # Dismiss popups
    _dismiss_all_popups(app, _log)

    _log("QB is ready!")
    return True


# ── Open store (used by QB sync tab) ─────────────────────────────────
def open_store(store_name, store_paths, qbw_match=None, password_key="pass1"):
    """Open a store: launch QB with file path, login."""
    if store_name not in store_paths:
        log(f"No .qbw path for '{store_name}'")
        return False

    qbw_path = store_paths[store_name]
    password = PASSWORDS.get(password_key, "")
    qb_exe = resolve_qb_executable()

    if not Path(qbw_path).exists():
        log(f"QB file not found: {qbw_path}")
        return False
    if not qb_exe:
        log("QuickBooks executable not found. Check QB_EXE_PATH or install path.")
        return False

    log(f"Opening store: {store_name}")
    log(f"  File: {qbw_path}")

    subprocess.Popen([str(qb_exe), qbw_path])
    time.sleep(10)

    from pywinauto import Application
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
        log(f"  Waiting for QB... ({(attempt + 1) * 5}s)")
        time.sleep(5)

    if not app:
        log("Failed to connect to QB")
        return False

    log("Connected to QB")

    time.sleep(3)
    logged_in = _do_login(app, password, log)
    if not logged_in:
        log("Login failed")
        return False

    _wait_for_ready(app, log)
    _dismiss_all_popups(app, log)

    log(f"{store_name} is ready!")
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


def _dismiss_all_popups(app, _log):
    """Close all popups/dialogs after login."""
    import pywinauto.keyboard as kb
    import pywinauto.mouse as mouse

    _log("Dismissing popups...")
    closed = 0
    time.sleep(3)

    for attempt in range(20):
        found = False
        try:
            win = app.top_window()

            # Check for Memorized Transactions dialog
            for search_root in [win]:
                try:
                    memo = search_root.child_window(title_re=".*Memorized Transactions.*")
                    if memo.exists(timeout=1):
                        clicked = False
                        for btn_title in ["Enter All Later", "Enter All  Later"]:
                            try:
                                btn = memo.child_window(title_re=f".*{btn_title}.*")
                                if btn.exists(timeout=1):
                                    btn.click_input()
                                    clicked = True
                                    break
                            except Exception:
                                pass

                        if not clicked:
                            try:
                                all_btns = memo.descendants(control_type="Button") + memo.descendants(control_type="Pane")
                                for b in all_btns:
                                    bt = b.window_text()
                                    if bt and "Later" in bt:
                                        b.click_input()
                                        clicked = True
                                        break
                            except Exception:
                                pass

                        if not clicked:
                            try:
                                rect = memo.rectangle()
                                mouse.click(coords=(rect.right - 15, rect.top + 15))
                                clicked = True
                            except Exception:
                                pass

                        if not clicked:
                            kb.send_keys("{ESCAPE}")

                        _log("  Closed: Memorized Transactions")
                        closed += 1
                        found = True
                        time.sleep(2)
                        break
                except Exception:
                    pass

            if found:
                continue

            # Check for any other popup
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

                clicked = False
                for btn_title in ["Enter All Later", "Remind me later",
                                  "Remind Me Later", "Cancel", "Close", "No",
                                  "Not Now", "Skip", "Later", "No Thanks", "OK"]:
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

                if clicked:
                    _log(f"  Closed popup: '{ctitle}'")
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
