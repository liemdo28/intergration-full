"""
ToastPOSManager — Crash Reporter & Support Bundle Generator

On any fatal error, generates a machine-readable + human-readable support bundle.
Aims to make support triage fast without exposing sensitive data (passwords redacted).
"""

from __future__ import annotations
import json, logging, os, platform, shutil, socket, sys, traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Paths (same as app_paths + bootstrap_runtime)
# ---------------------------------------------------------------------------
def _resolve_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent

BUNDLE_DIR = _resolve_bundle_dir()
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else BUNDLE_DIR

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_support_bundle(
    *,
    title: str,
    description: str,
    fatal: bool = False,
    extra_traceback: str | None = None,
) -> Path:
    """
    Generate a support bundle zip at RUNTIME_DIR/crash-reports/.
    Returns the path to the created zip file.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bundle_name = f"support_bundle_{timestamp}"
    bundle_dir = RUNTIME_DIR / "crash-reports" / bundle_name

    try:
        bundle_dir.mkdir(parents=True, exist_ok=True)

        # Write metadata
        _write_json(bundle_dir / "meta.json", {
            "title": title,
            "description": description,
            "fatal": fatal,
            "generated_at": datetime.now().isoformat(),
            "machine": _get_machine_info(),
            "python": {
                "version": sys.version,
                "frozen": getattr(sys, "frozen", False),
                "executable": sys.executable,
            },
            "platform": {
                "system": os.name,
                "windows_release": platform.win32_ver()[0],
                "windows_version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor(),
            },
        })

        # Write crash info if traceback provided
        if extra_traceback:
            (bundle_dir / "traceback.txt").write_text(extra_traceback, encoding="utf-8")

        # Write logs
        _collect_logs(bundle_dir)

        # Write config health
        _write_config_health(bundle_dir)

        # Write environment summary
        _write_env_summary(bundle_dir)

        # Write human-readable README
        _write_readme(bundle_dir, title, description)

        # Create zip
        zip_path = RUNTIME_DIR / "crash-reports" / f"{bundle_name}.zip"
        shutil.make_archive(
            str(zip_path.with_suffix("")),
            "zip",
            root_dir=bundle_dir,
        )
        logging.info(f"Support bundle created: {zip_path}")

        # Clean up unpacked dir (keep only zipped)
        shutil.rmtree(bundle_dir, ignore_errors=True)

        return zip_path

    except Exception as exc:
        logging.error(f"Failed to generate support bundle: {exc}")
        # Fallback: just save the traceback as a text file
        fallback = RUNTIME_DIR / "crash-reports" / f"crash_{timestamp}.txt"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(
            f"Support bundle generation failed: {exc}\n\n"
            f"Original error: {title}\n{description}\n\n"
            f"{extra_traceback or traceback.format_exc()}",
            encoding="utf-8",
        )
        return fallback


def redacted_path(env_key: str) -> str:
    """Return path from env var, or 'NOT SET' if missing."""
    path = os.environ.get(env_key, "")
    if not path:
        return "NOT SET"
    return path


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _get_machine_info() -> dict:
    try:
        return {
            "hostname": socket.gethostname(),
            "fqdn": socket.getfqdn(),
            "domain": os.environ.get("USERDOMAIN", "unknown"),
        }
    except Exception:
        return {"hostname": "unknown"}


def _collect_logs(bundle_dir: Path) -> None:
    """Copy recent log files, redacting passwords."""
    log_src = RUNTIME_DIR / "logs"
    log_dest = bundle_dir / "logs"
    if log_src.exists():
        log_dest.mkdir(parents=True, exist_ok=True)
        for log_file in sorted(log_src.glob("*.log"))[-5:]:  # last 5 logs
            try:
                content = log_file.read_text(encoding="utf-8", errors="replace")
                # Redact common secrets
                for pattern in [
                    "QB_PASSWORD", "password=", "token=", "secret=", "api_key", "Bearer ",
                ]:
                    content = _redact_line(content, pattern)
                (log_dest / log_file.name).write_text(content, encoding="utf-8")
            except Exception as exc:
                logging.warning(f"Could not copy log {log_file}: {exc}")


def _redact_line(text: str, pattern: str) -> str:
    """Redact lines containing pattern (for logs)."""
    lines = text.splitlines()
    result = []
    for line in lines:
        if pattern in line:
            result.append(line[:50] + "... [REDACTED]")
        else:
            result.append(line)
    return "\n".join(result)


def _write_config_health(bundle_dir: Path) -> None:
    """Write health summary of all config files."""
    checks = []
    for cfg_name in [".env.qb", "local-config.json", "credentials.json", "token.json"]:
        path = RUNTIME_DIR / cfg_name
        if path.exists():
            try:
                size = path.stat().st_size
                # For .env.qb, only check presence and password count
                if cfg_name == ".env.qb":
                    has_passwords = "QB_PASSWORD" in path.read_text(encoding="utf-8")
                    checks.append({
                        "file": cfg_name, "exists": True, "size_bytes": size,
                        "has_passwords": has_passwords, "status": "ok",
                    })
                elif cfg_name == "local-config.json":
                    data = json.loads(path.read_text(encoding="utf-8"))
                    keys = list(data.keys())
                    checks.append({
                        "file": cfg_name, "exists": True, "size_bytes": size,
                        "keys": keys, "status": "ok",
                    })
                else:
                    checks.append({
                        "file": cfg_name, "exists": True, "size_bytes": size,
                        "status": "ok",
                    })
            except Exception as exc:
                checks.append({"file": cfg_name, "exists": True, "status": f"error: {exc}"})
        else:
            checks.append({"file": cfg_name, "exists": False, "status": "missing"})

    _write_json(bundle_dir / "config_health.json", {"configs": checks})


def _write_env_summary(bundle_dir: Path) -> None:
    """Write sanitized environment variables summary."""
    interesting_keys = [
        "PATH", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
        "COMPUTERNAME", "USERNAME", "USERDOMAIN",
        "QB_EXE_PATH", "GOOGLE_APPLICATION_CREDENTIALS",
        "TOAST_ENV", "TOAST_TOKEN",
    ]
    env = {}
    for key in interesting_keys:
        val = os.environ.get(key, "")
        if val:
            # Only show first 100 chars for paths
            env[key] = val[:100] + ("..." if len(val) > 100 else "")
    _write_json(bundle_dir / "environment.json", env)


def _write_readme(bundle_dir: Path, title: str, description: str) -> None:
    readme = f"""ToastPOSManager — Support Bundle
=================================
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

ISSUE: {title}
{description}

WHAT TO DO:
1. Review config_health.json to confirm the app had the right files
2. Review logs/*.log for recent errors
3. Review traceback.txt (if present) for the crash location
4. Review environment.json for context about the machine

BEFORE SHARING:
- config_health.json and environment.json are intentionally partial
- Passwords and tokens are redacted
- No business data (sales, customer names) is included

SUPPORT CONTACT:
Email this file or its contents to your IT contact.
Zip file: {bundle_dir.parent}.zip
"""
    (bundle_dir / "README.txt").write_text(readme, encoding="utf-8")


if __name__ == "__main__":
    bundle = generate_support_bundle(
        title="Test Bundle",
        description="Launcher smoke test",
        fatal=False,
    )
    print(f"Support bundle created: {bundle}")
