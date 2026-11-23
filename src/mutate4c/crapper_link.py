"""Import crap4c from the environment or the sibling checkout.

Function names, namespaces, byte spans, and line spans come from crap4c, so
`.metrics/mutate` joins the same operations uml-viewer joins to
`.metrics/crap.edn`.
"""

from __future__ import annotations

import sys
from pathlib import Path

SIBLING = "crap4C"
PACKAGE = "crap4c"


def _sibling_src() -> Path:
    project = Path(__file__).resolve().parents[2]
    return project.parent / SIBLING / "src"


def ensure_crapper():
    try:
        import crap4c
    except ImportError:
        sibling = _sibling_src()
        if not (sibling / PACKAGE).is_dir():
            raise ImportError(
                "crap4c is not installed and was not found at "
                f"{sibling}. Clone or check out {SIBLING} next to mutate4C."
            ) from None
        sys.path.insert(0, str(sibling))
        import crap4c
    import crap4c.coverage
    import crap4c.discover
    import crap4c.languages
    import crap4c.languages.treesitter
    import crap4c.runners

    return crap4c
