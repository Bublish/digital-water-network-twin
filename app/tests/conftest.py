"""
Shared pytest config. Lets tests be run as `pytest` from the repo root.
"""
import sys
from pathlib import Path

# Put the repo root on sys.path so `from app.simulation...`, `from app.api...` etc resolve.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
