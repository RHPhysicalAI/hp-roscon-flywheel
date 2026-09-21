# This project was developed with assistance from AI tools.
"""Put the eval dashboard package on the import path."""
import sys
from pathlib import Path

# tests/eval_dashboard/conftest.py -> repo root -> src/eval-dashboard (hyphenated, so not importable as a package)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "eval-dashboard"))
