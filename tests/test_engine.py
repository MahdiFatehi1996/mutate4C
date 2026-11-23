import os
import threading

from mutate4c.edn import loads
from mutate4c.engine import (
    _carry_forward,
    _covered,
    _forms,
    _restore_if_dirty,
    mutate_file,
    restore_backups,
)
from mutate4c.metrics import snapshot_path
from mutate4c.model import History, PriorForm, Site
from mutate4c.runner import CommandResult
from mutate4c.workers import worker_count

SOURCE = """int add(int a, int b) {
    if (a > 0) {
        return a + b;
    }
    return 0;
}
"""


class FakeRunner:
    def __init__(self):
        self.verbose = False
        self.calls = 0
        self._lock = threading.Lock()

    def run(self, command, cwd, timeout):
        with self._lock:
            self.calls += 1
        text = (cwd / "src" / "demo.c").read_text(encoding="utf-8")
        ok = "a + b" in text
        return CommandResult(code=0 if ok else 1, timed_out=False, seconds=0.01, output="")


class KillMutants:
    verbose = False

    def run(self, command, cwd, timeout):
        return CommandResult(code=0 if timeout is None else 1, timed_out=False, seconds=0.01, output="")


def _mutate(path, tmp_path, runner, **overrides):
    values = dict(
        runner=runner,
        covered_lines=None,
        ignore_coverage=True,
        mutate_all=True,
        lines=None,
        test_command="fake",
        timeout_factor=10,
        mutation_warning=50,
        baselines={},
    )
    values.update(overrides)
    return mutate_file(path, tmp_path, **values)


def test_mutants_are_killed_or_kept_and_the_snapshot_is_differential(tmp_path):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text(SOURCE, encoding="utf-8")
    original = path.read_bytes()
    runner = FakeRunner()
    first = _mutate(path, tmp_path, runner)
    assert path.read_bytes() == original
    assert first.baseline_failed is False
    form = first.forms[0]
    assert form.id == "defn/add"
    assert form.namespace == "demo"
    # `>` and the two other constants survive. The executed `+` is killed.
    assert form.killed == 1
    assert form.survived == 3
    assert form.sites == 4
    assert form.killed + form.survived + form.uncovered == form.sites
    data = loads(snapshot_path(tmp_path, "demo").read_text(encoding="utf-8"))
    assert data["namespace"] == "demo"
    assert data["forms"][0]["id"] == "defn/add"
    assert data["forms"][0]["killed"] == 1
    assert data["forms"][0]["survived"] == 3
    first_calls = runner.calls

    second = _mutate(path, tmp_path, runner, mutate_all=False)
    assert second.forms[0].killed == 1
    assert second.forms[0].survived == 3
    # Baseline plus the three survivors. The killed mutant stays in the snapshot.
    assert runner.calls - first_calls == 4


def test_uncovered_sites_are_not_executed(tmp_path):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text(SOURCE, encoding="utf-8")
    runner = FakeRunner()
    result = _mutate(path, tmp_path, runner, covered_lines=set(), ignore_coverage=False)
    assert runner.calls == 0
    assert result.forms[0].uncovered == 4
    assert result.forms[0].killed == 0
    assert result.forms[0].sites == 4


def test_a_site_inside_a_covered_multi_line_statement_is_run(tmp_path):
    # LCOV marks only the line a statement starts on.
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text("int add(int a, int b) {\n    return (a\n            + b);\n}\n", encoding="utf-8")
    result = _mutate(path, tmp_path, KillMutants(), covered_lines={2}, ignore_coverage=False)
    assert result.forms[0].uncovered == 0
    assert result.forms[0].killed == 1


def test_a_failed_baseline_does_not_rewrite_metrics(tmp_path):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text(SOURCE, encoding="utf-8")

    class Red:
        verbose = False

        def run(self, command, cwd, timeout):
            return CommandResult(code=1, timed_out=False, seconds=0.01, output="nope")

    result = _mutate(path, tmp_path, Red())
    assert result.baseline_failed
    assert not snapshot_path(tmp_path, "demo").exists()


def test_worker_count_follows_sites_cores_and_the_requested_cap():
    cores = os.cpu_count() or 1
    assert worker_count(10, None) == min(10, cores)
    assert worker_count(10, 3) == min(3, cores)
    assert worker_count(2, 8) == min(2, cores)


def test_a_mutation_run_leaves_the_project_tree_unchanged(tmp_path):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text("int place(int x) { return x > 0; }\n", encoding="utf-8")
    original = path.read_text(encoding="utf-8")

    class Record:
        verbose = False

        def run(self, command, cwd, timeout):
            if timeout is not None:
                assert "mutation-workers" in cwd.as_posix()
                assert (cwd / "src" / "demo.c").read_text(encoding="utf-8") != original
            return CommandResult(code=0 if timeout is None else 1, timed_out=False, seconds=0.01, output="")

    _mutate(path, tmp_path, Record(), max_workers=2)
    assert path.read_text(encoding="utf-8") == original
    assert [item for item in (tmp_path / "target").rglob("*") if item.is_file()] == []
    run_dirs = list((tmp_path / "target" / "mutation-workers").glob("run-*"))
    assert run_dirs == []


def test_restore_backups_puts_an_interrupted_mutant_back(tmp_path):
    source = tmp_path / "src" / "demo.c"
    source.parent.mkdir()
    source.write_text("original\n", encoding="utf-8")
    backup = tmp_path / "target" / "mutator-backup" / "src" / "demo.c"
    backup.parent.mkdir(parents=True)
    backup.write_text("original\n", encoding="utf-8")
    source.write_text("mutated\n", encoding="utf-8")
    restored = restore_backups(tmp_path)
    assert restored == [source]
    assert source.read_text(encoding="utf-8") == "original\n"
    assert not backup.exists()


def test_a_dirty_file_is_put_back(tmp_path):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_bytes(b"one")
    _restore_if_dirty(path, b"clean")
    assert path.read_bytes() == b"clean"


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


def test_forms_are_ordered_by_span_and_count_uncovered_and_carried_sites(tmp_path, monkeypatch):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text("int place(int x) { return x > 0; }\n", encoding="utf-8")
    site = _site()
    assert _covered(site, None, False) is False
    assert _covered(site, {2}, False) is True
    assert _covered(site, None, True) is True

    spans = {
        ("demo.zebra", "defn/long"): (1, 10, "long", False),
        ("demo", "defn/short"): (3, 4, "short", False),
    }
    monkeypatch.setattr("mutate4c.engine.form_spans", lambda *args: spans)
    monkeypatch.setattr("mutate4c.engine.form_digests", lambda *args: {key: "d" for key in spans})
    ordered = _forms("x", path, tmp_path, "src/demo.c", [], {}, {}, None)
    assert [form.name for form in ordered] == ["long", "short"]

    owned = _site(namespace="demo", form_id="defn/place", mutation_id="m", line=2)
    place_span = {("demo", "defn/place"): (1, 3, "place", False)}
    monkeypatch.setattr("mutate4c.engine.form_spans", lambda *args: place_span)
    monkeypatch.setattr("mutate4c.engine.form_digests", lambda *args: {("demo", "defn/place"): "d"})
    counted = _forms("x", path, tmp_path, "src/demo.c", [owned], {"m": True}, {"m": "survived"}, {2})
    assert counted[0].survived == 1
    assert counted[0].killed == 0
    missing = _forms("x", path, tmp_path, "src/demo.c", [owned], {"m": True}, {}, None)
    assert missing[0].survived == 1

    history = History(
        forms={("demo", "defn/place"): PriorForm("demo", "defn/place", "old", "src/demo.c")},
        outcomes={"missing": "killed", "m": "killed"},
    )
    carried = _carry_forward([owned], {"m": True}, history, {("demo", "defn/place"): "new"})
    assert carried == {}
    uncovered = _carry_forward([owned], {"m": False}, history, {("demo", "defn/place"): "old"})
    assert uncovered == {}


def test_baseline_failure_shows_only_the_tail(tmp_path):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text(SOURCE, encoding="utf-8")

    class Red:
        verbose = False

        def run(self, command, cwd, timeout):
            lines = ["START"] + [f"M{i}" for i in range(1, 24)] + ["END"]
            return CommandResult(code=1, timed_out=False, seconds=0.01, output="\n".join(lines))

    failed = _mutate(path, tmp_path, Red())
    assert "M5" in failed.baseline_message
    assert "START" not in failed.baseline_message


def test_the_timeout_is_the_factor_times_the_baseline_with_a_two_second_floor(tmp_path):
    path = tmp_path / "src" / "demo.c"
    path.parent.mkdir()
    path.write_text("int place(int x) { return x > 0; }\n", encoding="utf-8")

    class Clock:
        verbose = False

        def __init__(self, seconds):
            self.seconds = seconds
            self.timeouts = []
            self._lock = threading.Lock()

        def run(self, command, cwd, timeout):
            with self._lock:
                self.timeouts.append(timeout)
            return CommandResult(code=0 if timeout is None else 1, timed_out=False, seconds=self.seconds, output="")

    slow = Clock(seconds=30)
    _mutate(path, tmp_path, slow)
    assert slow.timeouts[0] is None
    assert slow.timeouts[1] == 300

    fast = Clock(seconds=0.01)
    _mutate(path, tmp_path, fast)
    assert fast.timeouts[0] is None
    assert fast.timeouts[1] == 2.0


def test_a_missing_backup_is_not_an_error(tmp_path):
    original = tmp_path / "src" / "demo.c"
    original.parent.mkdir()
    original.write_text(SOURCE, encoding="utf-8")

    class Drop:
        verbose = False

        def run(self, command, cwd, timeout):
            if timeout is not None:
                for backup in (cwd / "target" / "mutator-backup").rglob("*"):
                    if backup.is_file():
                        backup.unlink()
            return CommandResult(code=0 if timeout is None else 1, timed_out=False, seconds=0.01, output="")

    _mutate(original, tmp_path, Drop())
    assert original.read_text(encoding="utf-8") == SOURCE
