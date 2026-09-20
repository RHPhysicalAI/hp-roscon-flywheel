# This project was developed with assistance from AI tools.
"""Put the fleet renderer's modules on the import path."""
import sys
from pathlib import Path

# tests/fleet_renderer/conftest.py -> repo root -> src/fleet-renderer (hyphenated, so not importable as a package)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "fleet-renderer"))
