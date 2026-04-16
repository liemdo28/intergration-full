# -*- mode: python ; coding: utf-8 -*-
"""
ToastPOSManager — PyInstaller Build Specification
=================================================

Upgrades over the baseline spec:
  - launcher.py as entry point (bootstrap + safe-mode run before app.py)
  - Full recursive folder bundle: Map, docs, audit-logs, recovery-backups
  - All critical runtime templates + version.json as data assets
  - Playwright chromium browser resolved from the playwright cache at
    build time and included as a binary so the packaged app needs no
    additional runtime browser download
  - Complete hidden-imports list covering Win32/COM, UI, Excel, playwright,
    Google Drive, and every app module that is imported dynamically
  - Excludes for large unused stdlib and third-party packages to keep the
    bundle lean (matplotlib / numpy / pandas / scipy / PIL / tkinter)
  - console=False → true GUI app, no terminal window on launch
  - icon + Windows version metadata: paths are resolved at build time;
    absence of the files is non-fatal (conditionals guard every optional arg)
"""
from pathlib import Path

project_dir = Path(SPECPATH)

# ---------------------------------------------------------------------------
# Recursive folder datas  (must be defined before datas uses them)
# ---------------------------------------------------------------------------
def _folder_data(name, dest=None):
    """Return a (src, dst) tuple if folder exists, else None."""
    folder = project_dir / name
    if folder.exists():
        return (str(folder), dest or name)
    return None


# ---------------------------------------------------------------------------
# All data assets to bundle
# ---------------------------------------------------------------------------
datas = [
    # --- Config templates / JSON manifests ---
    (str(project_dir / "Map"), "Map"),
    (str(project_dir / "qb-mapping.json"), "."),
    (str(project_dir / ".env.qb.example"), "."),
    (str(project_dir / "local-config.example.json"), "."),
    (str(project_dir / "credentials.json"), "."),
    (str(project_dir / "version.json"), "."),
    # --- Markdown docs ---
    (str(project_dir / "README.md"), "."),
    # --- Runtime template folders (recursive copy via TOC) ---
]

# Append recursive folder entries
_folder_datas = list(filter(None, [
    _folder_data("Map",        "Map"),
    _folder_data("docs",       "docs"),
    _folder_data("audit-logs",         "audit-logs"),
    _folder_data("recovery-backups",  "recovery-backups"),
]))
datas.extend(f for f in _folder_datas if f)

# ---------------------------------------------------------------------------
# Playwright chromium browser
# Resolve the browser executable from the playwright installation cache at
# build time so the frozen app does not need to download it at runtime.
# ---------------------------------------------------------------------------
playwright_browser_path = None
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        playwright_browser_path = Path(pw.chromium.executable_path).resolve()
except Exception:
    pass

binaries = []
if playwright_browser_path and playwright_browser_path.exists():
    # Include the entire browser directory — playwright stores supporting
    # files alongside the executable.
    browser_dir = playwright_browser_path.parent
    binaries.append((str(browser_dir), "playwright_browser"))
    # Bundle the app's own toast-reports output dir and logs dir
    datas.append((str(project_dir / "toast-reports"), "toast-reports"))
    datas.append((str(project_dir / "logs"), "logs"))

# ---------------------------------------------------------------------------
# All hidden imports
# PyInstaller cannot see imports made via importlib, __import__, or
# conditional branches; every name listed here is guaranteed to be present
# in the frozen binary regardless of how the runtime code loads it.
# ---------------------------------------------------------------------------
hiddenimports = [
    # ---- Windows COM / QuickBooks ----------------------------------------
    "win32timezone",
    "win32com",
    "win32com.client",
    "pythoncom",
    # ---- UI framework -----------------------------------------------------
    "customtkinter",
    "tkcalendar",
    "tkcalendar.calendario",
    "tkcalendar.dateconfig",
    # ---- Excel / openpyxl -------------------------------------------------
    "openpyxl",
    "openpyxl.cell",
    "openpyxl.styles",
    "openpyxl.utils",
    "et_xmlfile",
    # ---- Process monitoring -----------------------------------------------
    "psutil",
    # ---- QB automation ----------------------------------------------------
    "pywinauto",
    "pywinauto.application",
    # ---- Playwright browser-automation -----------------------------------
    "playwright.sync_api",
    "playwright._impl._sync_base",
    # ---- Google Drive API -------------------------------------------------
    "googleapiclient.discovery",
    "google.auth",
    "google.oauth2",
    "httplib2",
    # ---- App bootstrap / launcher (Phase 1 — runs before anything else) --
    "bootstrap_runtime",
    "crash_reporter",
    "safe_mode",
    "runtime_manifest",
    "first_run_wizard",
    "launcher",
    # ---- App core modules -------------------------------------------------
    "app_paths",
    "diagnostics",
    "toast_reports",
    "toast_downloader",
    "report_inventory",
    "report_coverage_validator",
    "date_parser",
    "qb_client",
    "qb_sync",
    "qb_automate",
    "gdrive_service",
    "sync_ledger",
    "integration_status",
    "marketplace_sync",
    "agentai_sync",
    "mapping_maintenance",
    "delete_policy",
    "recovery_center",
    "audit_utils",
]

# ---------------------------------------------------------------------------
# Excluded packages
# These are large optional dependencies that are never imported by this app.
# Excluding them reduces bundle size and startup time.
# ---------------------------------------------------------------------------
excludes = [
    "matplotlib",
    "numpy",
    "pandas",
    "scipy",
    "PIL",
    "tkinter",
]

# ---------------------------------------------------------------------------
# Optional icon
# ---------------------------------------------------------------------------
_icon = (
    str(project_dir / "assets" / "icon.ico")
    if (project_dir / "assets" / "icon.ico").exists()
    else None
)

# ---------------------------------------------------------------------------
# Optional Windows version metadata
# ---------------------------------------------------------------------------
_version_file = (
    str(project_dir / "assets" / "version-info.txt")
    if (project_dir / "assets" / "version-info.txt").exists()
    else None
)

# ---------------------------------------------------------------------------
# PyInstaller Analysis
# ---------------------------------------------------------------------------
a = Analysis(
    ["launcher.py"],          # entry point: bootstrap → safe_mode → app
    pathex=[str(project_dir)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

# ---------------------------------------------------------------------------
# EXE (onefile-ish: bootloader + embedded archive)
# ---------------------------------------------------------------------------
exe_kwargs = dict(
    pyz,
    a.scripts,
    [],
    exclude_binaries=False,
    name="ToastPOSManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,           # GUI app — no terminal window
)

if _icon:
    exe_kwargs["icon"] = _icon
if _version_file:
    exe_kwargs["version"] = _version_file

exe = EXE(**exe_kwargs)

# ---------------------------------------------------------------------------
# COLLECT (on-disk distribution: exe + all dependencies in one folder)
# ---------------------------------------------------------------------------
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=["vcruntime140.dll", "vcruntime140_1.dll"],
    name="ToastPOSManager",
)
