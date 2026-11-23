"""Run mutants in isolated project copies.

Each worker is a directory under target/mutation-workers. Project files are
linked in; where a link is not permitted the file or directory is copied.
The file under test is a private copy, so workers can mutate it at the same
time without touching the original tree. The overlay is removed when the
file's mutants finish.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from threading import Lock

from mutate4c.model import Site
from mutate4c.runner import Command
from mutate4c.sites import apply_site

# Build output, caches, and the worker tree itself must not be shared. A link
# to ``target`` would point a worker at the directory that contains the worker.
SKIP_LINK = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".venv",
        "venv",
        "target",
        "dist",
        "build",
        "out",
        "coverage",
        ".metrics",
        "CMakeFiles",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".cache",
    }
)
SKIP_PREFIXES = ("cmake-build-",)

CONFIGS = (
    "CMakeLists.txt",
    "CMakePresets.json",
    "CMakeUserPresets.json",
    "Makefile",
    "makefile",
    "GNUmakefile",
)


def worker_count(site_count: int, requested: int | None) -> int:
    """The smaller of the sites, the cores, and an explicit --max-workers."""

    processors = os.cpu_count() or 1
    limit = processors if requested is None else requested
    return max(1, min(site_count, processors, limit))


def new_run_dir(root: Path) -> Path:
    directory = root / "target" / "mutation-workers" / f"run-{uuid.uuid4()}"
    directory.mkdir(parents=True)
    return directory


def _copy_fallback(link: Path, target: Path) -> None:
    """Windows without the symlink privilege copies instead of links."""

    if target.is_dir():
        shutil.copytree(target, link, symlinks=True)
        return
    shutil.copy2(target, link)


def symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or link.exists():
        link.unlink()
    try:
        link.symlink_to(target.resolve())
    except OSError:
        _copy_fallback(link, target)


def delete_tree(path: Path, attempts: int = 20, sleep=time.sleep) -> None:
    """Remove a worker tree without following a link into the real project.

    A test tool can still be flushing a report when the client exits, so a
    directory that looked empty can gain a file before ``rmdir``. Retry the
    walk instead of aborting the file's mutation run.
    """

    last: OSError | None = None
    for attempt in range(attempts):
        try:
            _delete_once(path)
            return
        except OSError as error:
            last = error
            if attempt + 1 < attempts:
                sleep(0.25 * (attempt + 1))
    if last is not None:
        raise last


def _delete_once(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if not path.exists():
        return
    if path.is_dir():
        for child in list(path.iterdir()):
            _delete_once(child)
        path.rmdir()
        return
    path.unlink(missing_ok=True)


def _copy_configs(worker: Path, root: Path, relative: str) -> None:
    for name in CONFIGS:
        source = root / name
        if not source.is_file():
            continue
        if name == relative:
            continue
        (worker / name).write_bytes(source.read_bytes())


def _skipped_name(name: str) -> bool:
    return name in SKIP_LINK or name.startswith(SKIP_PREFIXES)


def _link_children(worker_dir: Path, real_dir: Path, skip: set[str]) -> None:
    if not real_dir.is_dir():
        return
    for child in real_dir.iterdir():
        if child.name in skip or _skipped_name(child.name):
            continue
        destination = worker_dir / child.name
        if destination.exists() or destination.is_symlink():
            continue
        symlink(destination, child)


def _overlay(worker: Path, root: Path, relative: str, original: bytes) -> None:
    """Real directories along the source path. Every sibling is a link."""

    segments = tuple(relative.split("/"))
    for index, _segment in enumerate(segments[:-1]):
        rel_dir = Path(*segments[: index + 1])
        _link_children(worker / rel_dir, root / rel_dir, {segments[index + 1]})
    destination = worker.joinpath(*segments)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(original)


def create_workers(
    base: Path, root: Path, relative: str, original: bytes, count: int
) -> list[Path]:
    created = []
    first = relative.split("/", 1)[0]
    for index in range(count):
        worker = base / f"worker-{index}"
        worker.mkdir(parents=True, exist_ok=True)
        _copy_configs(worker, root, relative)
        _link_children(worker, root, {first})
        _overlay(worker, root, relative, original)
        created.append(worker)
    return created


def mapped_cwd(worker: Path, root: Path, cwd: Path) -> Path:
    try:
        relative = cwd.resolve().relative_to(root.resolve())
    except ValueError:
        return worker
    return (worker / relative).resolve()


def _execute(
    directory: Path,
    site: Site,
    root: Path,
    relative: str,
    original: bytes,
    runner,
    command: Command,
    cwd: Path,
    timeout: float,
    file_key: str,
) -> str:
    current = original[site.start : site.end].decode("utf-8")
    if current != site.original:
        print(
            f"Skipped {file_key}:{site.line} {site.description}; source bytes moved",
            file=sys.stderr,
        )
        return "survived"
    destination = directory / relative
    mutated = apply_site(original.decode("utf-8"), site.start, site.end, site.mutant)
    destination.write_bytes(mutated.encode("utf-8"))
    try:
        result = runner.run(command, mapped_cwd(directory, root, cwd), timeout)
    finally:
        destination.write_bytes(original)
    if result.timed_out or result.code != 0:
        return "killed"
    return "survived"


def _drain(
    directory: Path,
    pending: Queue,
    lock: Lock,
    outcomes: dict[str, str],
    root: Path,
    relative: str,
    original: bytes,
    runner,
    command: Command,
    cwd: Path,
    timeout: float,
    file_key: str,
) -> None:
    while True:
        try:
            site = pending.get_nowait()
        except Empty:
            return
        if runner.verbose:
            with lock:
                _report(directory.name, file_key, site)
        status = _execute(
            directory,
            site,
            root,
            relative,
            original,
            runner,
            command,
            cwd,
            timeout,
            file_key,
        )
        with lock:
            outcomes[site.mutation_id] = status


def _report(worker: str, file_key: str, site: Site) -> None:
    print(f"[{worker}] {file_key}:{site.line} {site.description}", file=sys.stderr)


def _run_all(
    directories: list[Path],
    sites: list[Site],
    outcomes: dict[str, str],
    root: Path,
    relative: str,
    original: bytes,
    runner,
    command: Command,
    cwd: Path,
    timeout: float,
    file_key: str,
) -> None:
    pending: Queue = Queue()
    for site in sites:
        pending.put(site)
    lock = Lock()
    with ThreadPoolExecutor(max_workers=len(directories)) as pool:
        futures = [
            pool.submit(
                _drain,
                directory,
                pending,
                lock,
                outcomes,
                root,
                relative,
                original,
                runner,
                command,
                cwd,
                timeout,
                file_key,
            )
            for directory in directories
        ]
        for future in futures:
            future.result()


def run_mutants(
    root: Path,
    source: Path,
    original: bytes,
    sites: list[Site],
    max_workers: int | None,
    runner,
    command: Command,
    cwd: Path,
    timeout: float,
    file_key: str,
    outcomes: dict[str, str],
) -> None:
    """Mutate ``sites`` in parallel overlays of ``root``. The source stays put."""

    if not sites:
        return
    base = new_run_dir(root)
    try:
        relative = source.resolve().relative_to(root.resolve()).as_posix()
        directories = create_workers(
            base, root, relative, original, worker_count(len(sites), max_workers)
        )
        _run_all(
            directories,
            sites,
            outcomes,
            root,
            relative,
            original,
            runner,
            command,
            cwd,
            timeout,
            file_key,
        )
    finally:
        delete_tree(base)
