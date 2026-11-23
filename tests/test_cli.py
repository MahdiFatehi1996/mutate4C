import subprocess
import sys

import pytest

from mutate4c.cli import (
    GitStatusError,
    _changed_files,
    _is_source,
    _prepare_coverage,
    parse_args,
    run,
    select_files,
)
from mutate4c.crapper_link import ensure_crapper
from mutate4c.edn import loads
from mutate4c.metrics import snapshot_path

SOURCE = """int add(int a, int b) {
    if (a > 0) {
        return a + b;
    }
    return 0;
}
"""

ONE_SITE = "int add(int a, int b) { return a + b; }\n"


def _fixture(tmp_path, source=SOURCE):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "demo.c").write_text(source, encoding="utf-8")
    return root


def _check(root, condition, log=None):
    lines = ["import pathlib, sys"]
    if log is not None:
        lines.append(f"pathlib.Path({str(log)!r}).open('a').write('x\\n')")
    lines.append("text = pathlib.Path('src/demo.c').read_text()")
    lines.append(f"sys.exit(0 if {condition} else 1)")
    (root / "check.py").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _command():
    return f'"{sys.executable}" check.py'


def _log_lines(path):
    if not path.exists():
        return 0
    return len(path.read_text(encoding="utf-8").splitlines())


def _site_lines(text):
    return [line for line in text.splitlines() if " -> " in line and "demo.c:" in line]


def test_help_names_the_command_and_c_toolchain(capsys):
    options = parse_args(["--help"])
    assert options.exit_code == 0
    assert "mutate4c" in options.message
    assert "uml-viewer" in options.message
    assert "gcovr" in options.message
    assert "cmake-build-*" in options.message
    assert "ctest --output-on-failure" in options.message
    assert run(["--help"]) == 0
    captured = capsys.readouterr()
    assert "Usage: mutate4c" in captured.out
    assert captured.err == ""


def test_conflicting_flags_are_usage_errors(capsys):
    assert parse_args(["--mutate-all", "--since-last-run"]).exit_code == 1
    assert parse_args(["--scan", "--max-workers", "2"]).exit_code == 1
    assert parse_args(["--no-coverage", "--coverage-command", "x"]).exit_code == 1
    assert parse_args(["--lines", "0"]).exit_code == 1
    assert parse_args(["--max-workers", "0"]).exit_code == 1
    assert parse_args(["--not-a-flag"]).exit_code == 1
    assert run(["--mutate-all", "--since-last-run"]) == 1
    captured = capsys.readouterr()
    assert "cannot be combined" in captured.err
    assert captured.out == ""


def test_scan_counts_a_hand_counted_fixture_and_writes_nothing(tmp_path, monkeypatch, capsys):
    root = _fixture(tmp_path)
    monkeypatch.chdir(tmp_path)
    code = run(["--scan", "--no-coverage", "--root", str(root), "src/demo.c"])
    captured = capsys.readouterr()
    assert code == 0
    assert "Scan: 4 mutation sites in src/demo.c" in captured.out
    assert _site_lines(captured.out) == [
        "  src/demo.c:2 > -> >=  [defn/add]",
        "  src/demo.c:2 0 -> 1  [defn/add]",
        "  src/demo.c:3 + -> -  [defn/add]",
        "  src/demo.c:5 0 -> 1  [defn/add]",
    ]
    assert not (root / ".metrics").exists()
    assert not (root / "target").exists()


def test_scan_skips_a_non_utf8_source(tmp_path, capsys):
    root = _fixture(tmp_path)
    path = root / "src" / "latin1.c"
    path.write_bytes(b"int f(void) { /* caf\xe9 */ return 0; }\n")
    code = run(["--scan", "--no-coverage", "--root", str(root), "src/latin1.c"])
    captured = capsys.readouterr()
    assert code == 0
    assert "Skipping src/latin1.c: file is not UTF-8" in captured.err
    assert "Scan:" not in captured.out


def test_changed_selects_a_dirty_source_file(tmp_path, monkeypatch, capsys):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text(ONE_SITE, encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "src/demo.c"], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    code = run(["--scan", "--no-coverage", "--changed", "--root", str(tmp_path)])
    captured = capsys.readouterr()
    assert code == 0
    assert "demo.c:1" in captured.out


def test_a_directory_argument_skips_a_named_test_file(tmp_path, capsys):
    src = tmp_path / "pkg" / "app.c"
    src.parent.mkdir()
    src.write_text(ONE_SITE, encoding="utf-8")
    test = tmp_path / "pkg" / "test_app.c"
    test.write_text("int test_place(void) { return 0; }\n", encoding="utf-8")
    header = tmp_path / "pkg" / "app.h"
    header.write_text("int add(int a, int b);\n", encoding="utf-8")
    code = run(
        ["--scan", "--no-coverage", "--root", str(tmp_path), str(tmp_path / "pkg"), str(test)]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "Skipping test file" in captured.err
    assert "app.c:1" in captured.out
    assert "test_app.c" not in captured.out


def test_a_full_run_kills_and_survives_then_restores_the_source(tmp_path, capsys):
    root = _fixture(tmp_path)
    original = (root / "src" / "demo.c").read_bytes()
    _check(root, '"a + b" in text')
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--root",
            str(root),
            "--test-command",
            _command(),
            "--max-workers",
            "2",
            "src/demo.c",
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    site_lines = _site_lines(captured.out)
    assert [line.split()[0] for line in site_lines] == ["SURVIVED", "SURVIVED", "KILLED", "SURVIVED"]
    assert [line.split()[1] for line in site_lines] == [
        "src/demo.c:2",
        "src/demo.c:2",
        "src/demo.c:3",
        "src/demo.c:5",
    ]
    assert (root / "src" / "demo.c").read_bytes() == original
    assert list((root / "target" / "mutation-workers").glob("run-*")) == []

    snapshot = loads(snapshot_path(root, "demo").read_text(encoding="utf-8"))
    assert snapshot["version"] == 1
    assert snapshot["source"] == "src/demo.c"
    assert snapshot["namespace"] == "demo"
    form = snapshot["forms"][0]
    assert form["id"] == "defn/add"
    assert form["kind"] == "defn"
    assert form["file"] == "src/demo.c"
    assert form["line"] == 1
    assert form["end-line"] == 6
    assert form["killed"] == 1
    assert form["survived"] == 3
    assert form["uncovered"] == 0
    assert form["sites"] == 4
    assert set(snapshot["outcomes"].values()) == {"killed", "survived"}

    again = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--root",
            str(root),
            "--test-command",
            _command(),
            "src/demo.c",
        ]
    )
    repeat = capsys.readouterr()
    assert again == 3
    assert [line.split()[0] for line in _site_lines(repeat.out)] == [
        "SURVIVED",
        "SURVIVED",
        "KILLED",
        "SURVIVED",
    ]


def test_a_baseline_failure_aborts_before_mutation(tmp_path, capsys):
    root = _fixture(tmp_path)
    _check(root, "False")
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--root",
            str(root),
            "--test-command",
            _command(),
            "src/demo.c",
        ]
    )
    captured = capsys.readouterr()
    assert code == 2
    assert "Baseline failed" in captured.err
    assert "KILLED" not in captured.out
    assert not snapshot_path(root, "demo").exists()
    assert (root / "src" / "demo.c").read_text(encoding="utf-8") == SOURCE


def test_a_timeout_kills_the_mutant(tmp_path, capsys):
    root = _fixture(tmp_path, ONE_SITE)
    _check(root, '"a + b" in text or __import__("time").sleep(30)')
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--root",
            str(root),
            "--test-command",
            _command(),
            "--max-workers",
            "1",
            "src/demo.c",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert [line.split()[0] for line in _site_lines(captured.out)] == ["KILLED"]
    snapshot = loads(snapshot_path(root, "demo").read_text(encoding="utf-8"))
    assert snapshot["forms"][0]["killed"] == 1
    assert snapshot["forms"][0]["survived"] == 0
    assert (root / "src" / "demo.c").read_text(encoding="utf-8") == ONE_SITE


def test_coverage_marks_unhit_sites_uncovered(tmp_path, capsys):
    root = _fixture(tmp_path)
    report = root / "target" / "coverage" / "c" / "lcov.info"
    report.parent.mkdir(parents=True)
    report.write_text(
        "SF:src/demo.c\nDA:1,1\nDA:2,1\nDA:3,0\nDA:4,0\nDA:5,0\nDA:6,1\nend_of_record\n",
        encoding="utf-8",
    )
    _check(root, '"a + b" in text')
    code = run(
        [
            "--reuse-coverage",
            "--mutate-all",
            "--root",
            str(root),
            "--test-command",
            _command(),
            "src/demo.c",
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    site_lines = _site_lines(captured.out)
    assert [line.split()[0] for line in site_lines] == [
        "SURVIVED",
        "SURVIVED",
        "UNCOVERED",
        "UNCOVERED",
    ]
    snapshot = loads(snapshot_path(root, "demo").read_text(encoding="utf-8"))
    form = snapshot["forms"][0]
    assert form["survived"] == 2
    assert form["uncovered"] == 2
    assert form["killed"] == 0


def test_a_missing_coverage_report_makes_every_site_uncovered(tmp_path, capsys):
    root = _fixture(tmp_path)
    _check(root, '"a + b" in text')
    code = run(
        [
            "--reuse-coverage",
            "--mutate-all",
            "--root",
            str(root),
            "--test-command",
            _command(),
            "src/demo.c",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert "No coverage data for src/demo.c" in captured.err
    assert [line.split()[0] for line in _site_lines(captured.out)] == ["UNCOVERED"] * 4
    snapshot = loads(snapshot_path(root, "demo").read_text(encoding="utf-8"))
    assert snapshot["forms"][0]["uncovered"] == 4


def test_since_last_run_skips_unchanged_killed_sites(tmp_path, capsys):
    root = _fixture(tmp_path)
    log = tmp_path / "runs.log"
    _check(root, '"a + b" in text', log=log)
    base = [
        "--no-coverage",
        "--root",
        str(root),
        "--test-command",
        _command(),
        "--max-workers",
        "1",
        "src/demo.c",
    ]
    assert run(["--mutate-all", *base]) == 3
    capsys.readouterr()
    assert _log_lines(log) == 5

    assert run(["--since-last-run", *base]) == 3
    capsys.readouterr()
    assert _log_lines(log) - 5 == 4

    assert run(["--mutate-all", *base]) == 3
    capsys.readouterr()
    assert _log_lines(log) - 9 == 5


def test_lines_restricts_selection_and_rejects_zero(tmp_path, capsys):
    root = _fixture(tmp_path)
    _check(root, '"a + b" in text', log=tmp_path / "runs.log")
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--lines",
            "3",
            "--root",
            str(root),
            "--test-command",
            _command(),
            "src/demo.c",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert [line.split()[1] for line in _site_lines(captured.out)] == ["src/demo.c:3"]
    assert run(["--lines", "0"]) == 1


def test_parallel_progress_names_the_worker_and_results_keep_source_order(tmp_path, capsys):
    root = _fixture(tmp_path)
    _check(root, '"a + b" in text')
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--verbose",
            "--root",
            str(root),
            "--test-command",
            _command(),
            "--max-workers",
            "2",
            "src/demo.c",
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    assert "[worker-" in captured.err
    positions = [line.split()[1] for line in _site_lines(captured.out)]
    assert positions == [
        "src/demo.c:2",
        "src/demo.c:2",
        "src/demo.c:3",
        "src/demo.c:5",
    ]
    assert list((root / "target" / "mutation-workers").glob("run-*")) == []


def test_a_failed_coverage_command_does_not_read_the_report(tmp_path, capsys):
    root = _fixture(tmp_path)
    report = root / "target" / "coverage" / "c" / "lcov.info"
    report.parent.mkdir(parents=True)
    report.write_text("SF:src/demo.c\nDA:1,1\nDA:2,1\nend_of_record\n", encoding="utf-8")
    code = run(
        [
            "--root",
            str(root),
            "--coverage-command",
            "exit 3",
            "--test-command",
            "false",
            "src/demo.c",
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    assert "will not be read" in captured.err
    assert "Baseline failed" not in captured.err
    assert (root / "src" / "demo.c").read_text(encoding="utf-8") == SOURCE


def test_a_test_command_override_wins_over_a_cmake_project(tmp_path, capsys):
    root = _fixture(tmp_path, ONE_SITE)
    (root / "CMakeLists.txt").write_text("project(demo C)\n", encoding="utf-8")
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--root",
            str(root),
            "--test-command",
            "exit 0",
            "src/demo.c",
        ]
    )
    captured = capsys.readouterr()
    assert code == 3
    assert [line.split()[0] for line in _site_lines(captured.out)] == ["SURVIVED"]


def test_an_unplanned_project_fails_the_baseline(tmp_path, capsys):
    root = _fixture(tmp_path, ONE_SITE)
    code = run(["--no-coverage", "--mutate-all", "--root", str(root), "src/demo.c"])
    captured = capsys.readouterr()
    assert code == 2
    assert "Baseline failed" in captured.err
    assert not snapshot_path(root, "demo").exists()


def test_a_scan_does_not_restore_a_backup_and_a_run_does(tmp_path, capsys):
    root = _fixture(tmp_path, ONE_SITE)
    clean = "int add(int a, int b) { return a - b; }\n"
    backup = root / "target" / "mutator-backup" / "src" / "demo.c"
    backup.parent.mkdir(parents=True)
    backup.write_text(clean, encoding="utf-8")
    assert run(["--scan", "--no-coverage", "--root", str(root), "src/demo.c"]) == 0
    assert (root / "src" / "demo.c").read_text(encoding="utf-8") == ONE_SITE
    assert "+ -> -" in capsys.readouterr().out

    _check(root, "False")
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--root",
            str(root),
            "--test-command",
            _command(),
            "src/demo.c",
        ]
    )
    assert code == 2
    assert (root / "src" / "demo.c").read_text(encoding="utf-8") == clean


def test_existing_coverage_is_not_regenerated(monkeypatch, tmp_path):
    crapper = ensure_crapper()
    calls = []
    monkeypatch.setattr(crapper.runners, "run_coverage", lambda *args, **kwargs: calls.append(args))
    root = tmp_path
    _prepare_coverage(parse_args(["--no-coverage", "--root", str(root)]), root, [])
    _prepare_coverage(parse_args(["--reuse-coverage", "--root", str(root)]), root, [])
    _prepare_coverage(parse_args(["--scan", "--root", str(root)]), root, [])
    assert calls == []
    _prepare_coverage(parse_args(["--root", str(root)]), root, [])
    assert len(calls) == 1


def test_selection_joins_the_root_and_skips_unknown_files(tmp_path):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text(ONE_SITE, encoding="utf-8")
    note = tmp_path / "note.txt"
    note.write_text("hello\n", encoding="utf-8")
    crapper = ensure_crapper()
    assert _is_source(note, crapper) is False
    assert _is_source(path, crapper) is True
    options = parse_args(["--root", str(tmp_path), "src/demo.c"])
    assert select_files(options) == [path.resolve()]


def test_git_status_fails_closed(tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    code = run(["--changed", "--root", str(outside)])
    captured = capsys.readouterr()
    assert code == 128
    assert "not a git repository" in captured.err
    assert "No source files" not in captured.out


def test_a_static_function_writes_the_private_form_id(tmp_path, capsys):
    root = _fixture(tmp_path, "static int hide(void) { return 0; }\n")
    _check(root, '"static int hide" in text')
    code = run(
        [
            "--no-coverage",
            "--mutate-all",
            "--root",
            str(root),
            "--test-command",
            _command(),
            "src/demo.c",
        ]
    )
    capsys.readouterr()
    assert code == 3
    snapshot = loads(snapshot_path(root, "demo").read_text(encoding="utf-8"))
    assert snapshot["forms"][0]["id"] == "defn-/hide"
    assert snapshot["forms"][0]["kind"] == "defn-"
