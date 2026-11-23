"""Boundaries the main paths do not exercise.

Empty trees, odd snapshots, a misleading coverage report, and the report text
are checked here rather than duplicating the engine and CLI scenarios.
"""

import hashlib
import subprocess

import pytest

from mutate4c.cli import _changed_files, parse_args, run, select_files
from mutate4c.edn import dumps, loads
from mutate4c.engine import mutate_file, scan_file
from mutate4c.functions import (
    _mutation_field,
    _owner,
    digest,
    form_spans,
    is_private,
    mutation_file,
)
from mutate4c.metrics import (
    _drop_stale,
    _form_file,
    _form_file_only,
    _mentions_file,
    _outcomes_of,
    _remove_empty_dirs,
    _write_namespace,
    load_history,
    snapshot_path,
    write_results,
)
from mutate4c.model import FormResult, Site
from mutate4c.report import format_results, format_scan
from mutate4c.runner import CommandResult
from mutate4c.sites import _grammar


def _form(namespace, name, file_name, digest_text, killed=1, private=False, line=2):
    form_id = f"defn-/{name}" if private else f"defn/{name}"
    return FormResult(
        id=form_id,
        namespace=namespace,
        name=name,
        private=private,
        file=file_name,
        line=line,
        end_line=line + 2,
        digest=digest_text,
        killed=killed,
        survived=0,
        uncovered=0,
        sites=1,
    )


def _site(**overrides) -> Site:
    values = dict(
        file="src/demo.c",
        namespace="demo",
        form_id="defn/place",
        name="place",
        line=2,
        start=0,
        end=1,
        original=">",
        mutant=">=",
        category="comparison",
        mutation_id="m",
    )
    values.update(overrides)
    return Site(**values)


def test_score_and_description_follow_the_counts():
    even = _form("demo", "place", "a.c", "d", killed=1)
    even.survived = 1
    assert even.score == 0.5
    blank = _form("demo", "place", "a.c", "d", killed=0)
    assert blank.score is None
    perfect = _form("demo", "place", "a.c", "d", killed=1)
    assert perfect.score == 1.0
    assert _site(original=">", mutant=">=").description == "> -> >="
    assert _site(original="!", mutant="").description == "delete !"


def test_report_text_states_the_score_the_total_and_coverage():
    assert format_results([]) == "No functions to mutate.\n"
    mixed = _form("demo", "place", "a.c", "d", killed=1)
    mixed.survived = 1
    mixed.sites = 2
    text = format_results([mixed])
    assert "50.0%" in next(line for line in text.splitlines() if "place" in line)
    total = next(line for line in text.splitlines() if line.startswith("Total"))
    assert total.split() == ["Total", "1", "1", "0", "2", "50.0%"]

    perfect = _form("demo", "place", "a.c", "d", killed=1)
    perfect_total = next(line for line in format_results([perfect]).splitlines() if line.startswith("Total"))
    assert perfect_total.split()[-1] == "100.0%"
    idle = _form("demo", "place", "a.c", "d", killed=0)
    idle.sites = 2
    idle.uncovered = 2
    idle_total = next(line for line in format_results([idle]).splitlines() if line.startswith("Total"))
    assert idle_total.split()[-1] == "n/a"

    covered = format_scan("src/demo.c", [_site()], set(), {"m": True})
    missing = format_scan("src/demo.c", [_site()], set(), {})
    bare = format_scan("src/demo.c", [_site()], set(), {"m": False})
    assert "uncovered" not in covered
    assert "uncovered" not in missing
    assert "uncovered" in bare


def test_edn_errors_comments_sets_and_nil_keys():
    with pytest.raises(ValueError, match="empty EDN"):
        loads("")
    with pytest.raises(ValueError, match="empty EDN"):
        loads("; hi")
    with pytest.raises(ValueError, match="unexpected end of EDN"):
        loads("{")
    with pytest.raises(ValueError, match="unterminated string"):
        loads('"hi')
    with pytest.raises(ValueError, match="unterminated string escape"):
        loads('"\\')
    with pytest.raises(ValueError, match="empty keyword"):
        loads(":")
    assert loads("; kept\n12") == 12
    assert loads("#{1 2}") == {1, 2}
    assert loads("{nil 1}") == {None: 1}


def test_privacy_digests_owners_and_mutation_fields(tmp_path):
    assert is_private("c", "hide", "static int hide(void) { return 1; }\n", 1, 1) is True
    assert is_private("c", "show", "int show(void) { return 1; }\n", 1, 1) is False
    assert is_private("cobol", "show", "PLACE.\n", 1, 1) is False

    source = "int place(int x) {\n    return x > 0;\n}\n"
    chunk = "int place(int x) {\n    return x > 0;"
    assert digest(source, 1, 2) == hashlib.sha256(chunk.encode("utf-8")).hexdigest()

    class Fn:
        def __init__(self, name, start, end):
            self.name = name
            self.start_line = start
            self.end_line = end

    assert _owner([Fn("outer", 1, 10), Fn("inner", 8, 9)], 8).name == "inner"

    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    hidden = "static int hide(void) {\n    return 1;\n}\n"
    path.write_text(hidden, encoding="utf-8")
    assert list(form_spans(hidden, path, tmp_path).values())[0][3] is True

    assert _mutation_field("not-json", 0) is None
    assert _mutation_field('"hello"', 0) is None
    assert _mutation_field("[1]", 0) is None
    assert _mutation_field('["only"]', 1) is None
    assert mutation_file('["src/a.c","demo","defn/place",1,2,">",">="]') == "src/a.c"


def test_the_grammar_guard_accepts_only_c():
    assert _grammar("c", "src/demo.c") == "c"
    with pytest.raises(ValueError):
        _grammar("python", "src/demo.py")


def test_options_reject_bad_values_and_keep_flag_defaults(monkeypatch):
    assert parse_args(["--lines", "1"]).lines == {1}
    assert parse_args(["--lines", "2, 4"]).lines == {2, 4}
    assert parse_args(["--timeout-factor", "1"]).timeout_factor == 1
    assert parse_args(["--timeout-factor", "0"]).exit_code == 1
    assert parse_args(["--timeout-factor", "-1"]).exit_code == 1
    assert parse_args(["--use-existing-coverage"]).use_existing_coverage is True
    assert parse_args(["--reuse-coverage"]).use_existing_coverage is True
    assert parse_args(["--verbose"]).verbose is True
    assert parse_args(["--changed"]).changed is True
    assert parse_args(["--since-last-run"]).since_last_run is True
    assert parse_args(["--max-workers", "2"]).max_workers == 2
    assert parse_args(["--max-workers", "nope"]).exit_code == 1
    import sys

    monkeypatch.setattr(sys, "argv", ["prog", "--verbose"])
    options = parse_args(None)
    assert options.verbose is True
    assert options.positionals == []


def _init_repo(path):
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Dev"], cwd=path, check=True)


def test_git_status_keeps_real_names_and_fails_closed(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    source = "int place(int x) { return x > 0; }\n"
    keep = repo / "src" / "café.c"
    keep.parent.mkdir()
    keep.write_text(source, encoding="utf-8")
    nested = repo / "fresh" / "nested" / "new.c"
    nested.parent.mkdir(parents=True)
    nested.write_text(source, encoding="utf-8")
    spaced = repo / "src" / "my file.c"
    spaced.write_text(source, encoding="utf-8")
    gone = repo / "gone.c"
    gone.write_text(source, encoding="utf-8")
    old = repo / "src" / "old.c"
    old.write_text(source, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, capture_output=True)
    keep.write_text(source + "\n", encoding="utf-8")
    nested.write_text(source + "\n", encoding="utf-8")
    spaced.write_text(source + "\n", encoding="utf-8")
    gone.unlink()
    subprocess.run(
        ["git", "mv", "src/old.c", "src/new.c"], cwd=repo, check=True, capture_output=True
    )
    skipped = repo / "target" / "skip.c"
    skipped.parent.mkdir()
    skipped.write_text(source, encoding="utf-8")

    found = set(_changed_files(repo))
    assert keep.resolve() in found
    assert nested.resolve() in found
    assert spaced.resolve() in found
    assert (repo / "src" / "new.c").resolve() in found
    assert gone.resolve() not in found
    assert old.resolve() not in found

    chosen = select_files(parse_args(["--root", str(repo), "--changed"]))
    assert skipped.resolve() not in chosen
    assert keep.resolve() in chosen


def test_changed_from_a_subdirectory_stays_inside_it(tmp_path):
    repo = tmp_path / "repo"
    sub = repo / "sub"
    (sub / "src").mkdir(parents=True)
    (repo / "src").mkdir()
    _init_repo(repo)
    below = sub / "src" / "below.c"
    above = repo / "src" / "above.c"
    body = "int place(int x) { return x > 0; }\n"
    below.write_text(body, encoding="utf-8")
    above.write_text(body, encoding="utf-8")
    chosen = select_files(parse_args(["--root", str(sub), "--changed"]))
    assert chosen == [below.resolve()]


def test_source_root_walks_only_that_tree(tmp_path, capsys):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "lib").mkdir()
    (root / "src" / "a.c").write_text("int a(void) { return 1; }\n", encoding="utf-8")
    (root / "lib" / "b.c").write_text("int b(void) { return 1; }\n", encoding="utf-8")
    code = run(["--scan", "--no-coverage", "--root", str(root), "--source-root", "src"])
    captured = capsys.readouterr()
    assert code == 0
    assert "a.c" in captured.out
    assert "b.c" not in captured.out


def test_an_empty_tree_exits_zero(tmp_path, capsys):
    assert run(["--no-coverage", "--root", str(tmp_path)]) == 0
    assert "No source files" in capsys.readouterr().out


def test_backups_are_restored_byte_for_byte(tmp_path):
    from mutate4c.engine import restore_backups

    backup = tmp_path / "target" / "mutator-backup" / "a" / "b" / "demo.c"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"original\n")
    restored = restore_backups(tmp_path)
    assert (tmp_path / "a" / "b" / "demo.c").read_bytes() == b"original\n"
    assert restored == [tmp_path / "a" / "b" / "demo.c"]


def test_a_mutation_warning_uses_the_requested_threshold(tmp_path, capsys):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text("int place(int x) { return x > 0; }\n", encoding="utf-8")

    class Ok:
        verbose = False

        def run(self, command, cwd, timeout):
            return CommandResult(code=0, timed_out=False, seconds=0.01, output="")

    mutate_file(
        path,
        tmp_path,
        runner=Ok(),
        covered_lines=None,
        ignore_coverage=True,
        mutate_all=True,
        lines=None,
        test_command="fake",
        timeout_factor=10,
        mutation_warning=1,
        baselines={},
    )
    assert "WARNING: 2 covered mutations selected" in capsys.readouterr().err


def test_scan_file_marks_uncovered_sites(tmp_path):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text("int add(int a, int b) {\n    return (a\n            + b);\n}\n", encoding="utf-8")
    scanned = scan_file(path, tmp_path, covered_lines={2}, ignore_coverage=False, lines=None)
    assert "uncovered" not in scanned
    assert "+ -> -" in scanned
    ignored = scan_file(path, tmp_path, covered_lines={2}, ignore_coverage=True, lines=None)
    assert "uncovered" not in ignored


def test_an_unrelated_snapshot_is_left_in_place(tmp_path):
    legacy = tmp_path / ".metrics" / "mutate" / "legacy.edn"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        dumps({"namespace": "untouched", "source": "c.c", "forms": [], "outcomes": {}}) + "\n",
        encoding="utf-8",
    )
    write_results(tmp_path, "a.c", [_form("demo", "run", "a.c", "h")], {})
    assert legacy.is_file()


def test_snapshots_keep_other_files_empty_directories_and_odd_records(tmp_path):
    assert _outcomes_of({"outcomes": {"id": ":killed"}}) == {"id": "killed"}
    assert _form_file({"file": ""}, {"source": "a.c"}) == "a.c"
    assert _mentions_file({"forms": [{"file": "a.c"}]}, "a.c") is True
    assert _mentions_file({"forms": [{"file": "b.c"}]}, "a.c") is False
    assert _form_file_only({"source": "a.c", "forms": []}, "a.c") is True
    assert _form_file_only({"source": "b.c", "forms": []}, "a.c") is False
    assert _form_file_only({"forms": [{"file": "a.c"}]}, "a.c") is True
    assert _form_file_only({"forms": [{"file": "b.c"}]}, "a.c") is False

    assert _write_namespace(tmp_path, "ghost", [], {}, [], {}, "a.c") is None
    missing = tmp_path / "missing.edn"
    _drop_stale([(missing, {"namespace": "demo"})], {"demo"}, set(), "a.c")
    assert not missing.exists()

    write_results(tmp_path, "b.c", [_form("demo", "stop", "b.c", "hash-b")], {})
    write_results(tmp_path, "a.c", [], {})
    kept = loads(snapshot_path(tmp_path, "demo").read_text(encoding="utf-8"))
    assert kept["source"] == "b.c"
    assert kept["forms"][0]["id"] == "defn/stop"

    odd = tmp_path / ".metrics" / "mutate" / "odd.edn"
    odd.write_text(
        dumps({"namespace": "odd", "forms": [{"id": 1, "hash": "digest", "file": "a.c"}], "outcomes": {}})
        + "\n",
        encoding="utf-8",
    )
    assert load_history(tmp_path, "a.c").forms == {}

    base = tmp_path / "empty-metrics"
    nested = base / "empty" / "nested"
    nested.mkdir(parents=True)
    full = base / "full"
    full.mkdir()
    (full / "keep.edn").write_text("x", encoding="utf-8")
    _remove_empty_dirs(base)
    assert not nested.exists()
    assert not (base / "empty").exists()
    assert (full / "keep.edn").is_file()
    _remove_empty_dirs(tmp_path / "missing-metrics")
