"""Make this checkout and the sibling crap4C importable under plain pytest."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

for entry in (ROOT / "src", ROOT.parent / "crap4C" / "src"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))
