import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP_APP = ROOT / "desktop-app"

if str(DESKTOP_APP) not in sys.path:
    sys.path.insert(0, str(DESKTOP_APP))
