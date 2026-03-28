import sys
from pathlib import Path


def _resolve_bundle_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


BUNDLE_DIR = _resolve_bundle_dir()
APP_DIR = BUNDLE_DIR
RUNTIME_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else APP_DIR


def _asset_candidates(*parts: str) -> list[Path]:
    if getattr(sys, "frozen", False):
        return [RUNTIME_DIR.joinpath(*parts), BUNDLE_DIR.joinpath(*parts)]
    return [BUNDLE_DIR.joinpath(*parts)]


def app_path(*parts: str) -> Path:
    candidates = _asset_candidates(*parts)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def runtime_path(*parts: str) -> Path:
    return RUNTIME_DIR.joinpath(*parts)
