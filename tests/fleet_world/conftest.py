# This project was developed with assistance from AI tools.
"""Makes src/fleet-world and tools/fleet importable: the hyphen rules out a normal package import."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for folder in (ROOT / "src" / "fleet-world", ROOT / "tools" / "fleet"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
