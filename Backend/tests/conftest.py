"""
Shared pytest config. Lets tests be run as `pytest` from Backend/.
"""
import sys
from pathlib import Path

# Ensure `Backend/` is on sys.path so `from simulation...`, `from api...` etc resolve.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
