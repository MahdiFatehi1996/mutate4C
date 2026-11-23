"""Run a project's tests, and decide the command for a C project."""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

Command = str | list[str]


@dataclass(frozen=True)
class CommandResult:
    code: int
    timed_out: bool
    seconds: float
    output: str


def display_command(command: Command) -> str:
    """A shell-looking rendering. A list is quoted so it can be pasted."""

    if isinstance(command, str):
        return command
    return " ".join(shlex.quote(part) for part in command)


def _kill_tree(process) -> None:
    """Stop the command and anything it started.

    A shell string starts a shell, which starts the real test command. The
    child inherits the pipe, so killing only the shell would leave the pipe
    open and `communicate()` would keep waiting.
    """

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()


class CommandRunner:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    def run(self, command: Command, cwd: Path, timeout: float | None) -> CommandResult:
        """Run `command` in `cwd`.

        A list is an argument vector and does not go through a shell, so a
        path with spaces or metacharacters stays one argument. A string is a
        command the user typed in `--test-command` or `--coverage-command`.
        """

        if self.verbose:
            print(f"+ ({cwd}) {display_command(command)}", file=sys.stderr)
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                shell=isinstance(command, str),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                text=True,
            )
        except OSError as exc:
            seconds = time.monotonic() - started
            return CommandResult(code=127, timed_out=False, seconds=seconds, output=str(exc))
        try:
            output, _err = process.communicate(timeout=timeout)
            code = process.returncode if process.returncode is not None else 1
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_tree(process)
            output, _err = process.communicate()
            code = 124
            timed_out = True
        seconds = time.monotonic() - started
        return CommandResult(code=code, timed_out=timed_out, seconds=seconds, output=output or "")


def _makefile(root: Path) -> bool:
    return any((root / name).is_file() for name in ("Makefile", "makefile", "GNUmakefile"))


def test_plan(root: Path, override: str | None) -> tuple[Command, Path]:
    """The test command and the directory it runs in.

    A CMake project at the root runs `ctest`. A Make project runs
    `make test`. Anything else runs a command that fails, so every mutant is
    killed until the user supplies `--test-command`.
    """

    root = root.resolve()
    if override:
        return override, root
    if (root / "CMakeLists.txt").is_file():
        return ["ctest", "--output-on-failure"], root
    if _makefile(root):
        return ["make", "test"], root
    return ["false"], root
