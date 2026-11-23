"""Decide which C source lines the tests already exercise.

Coverage is LCOV from crap4c's bundle: `target/coverage/**/lcov.info` and
`coverage/**/lcov.info`, merged. A file the report never mentions has no
coverage data, and the engine treats its sites as uncovered.
"""

from __future__ import annotations

from pathlib import Path

from mutate4c.crapper_link import ensure_crapper


def _relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _project_sources(root: Path, crapper) -> list[str]:
    found = []
    for path in crapper.discover.iter_source_files([root]):
        relative = _relative(root, path)
        if relative not in found:
            found.append(relative)
    return found


def _record(root: Path, source_path: Path, crapper):
    bundle = crapper.coverage.load_bundle(root)
    if not bundle.lcov:
        return None
    bundle.bind_sources(_project_sources(root, crapper))
    return crapper.coverage._lookup(bundle.lcov, _relative(root, source_path), bundle.source_parts)


def covered_lines(root: Path, source_path: Path) -> set[int] | None:
    """Lines with a hit, or None when this file is absent from coverage."""

    crapper = ensure_crapper()
    root = root.resolve()
    record = _record(root, Path(source_path), crapper)
    if record is None:
        return None
    return {number for number, (covered, _total) in record.items() if covered > 0}
