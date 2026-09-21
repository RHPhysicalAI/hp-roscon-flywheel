# This project was developed with assistance from AI tools.
"""Put the tenant metrics exporter's modules on the import path."""
import sys
from pathlib import Path

# tests/tenant_metrics/conftest.py -> repo root -> src/tenant-metrics (hyphenated, so not importable as a package)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src" / "tenant-metrics"))
