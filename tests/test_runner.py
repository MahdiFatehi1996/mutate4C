import sys

from mutate4c.runner import CommandRunner, display_command
from mutate4c.runner import test_plan as plan_command


def test_a_cmake_project_runs_ctest_at_the_root(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(demo C)\n", encoding="utf-8")
    command, directory = plan_command(tmp_path, None)
    assert command == ["ctest", "--output-on-failure"]
    assert directory == tmp_path.resolve()


def test_a_make_project_runs_make_test_at_the_root(tmp_path):
    (tmp_path / "Makefile").write_text("test:\n\ttrue\n", encoding="utf-8")
    command, directory = plan_command(tmp_path, None)
    assert command == ["make", "test"]
    assert directory == tmp_path.resolve()

    lower = tmp_path / "lower"
    lower.mkdir()
    (lower / "makefile").write_text("test:\n\ttrue\n", encoding="utf-8")
    assert plan_command(lower, None)[0] == ["make", "test"]


def test_cmake_wins_over_make(tmp_path):
    (tmp_path / "CMakeLists.txt").write_text("project(demo C)\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("test:\n\ttrue\n", encoding="utf-8")
    assert plan_command(tmp_path, None)[0] == ["ctest", "--output-on-failure"]


def test_a_project_without_a_plan_fails(tmp_path):
    assert plan_command(tmp_path, None) == (["false"], tmp_path.resolve())


def test_an_override_runs_in_the_project_root(tmp_path):
    command, directory = plan_command(tmp_path, "make check")
    assert command == "make check"
    assert directory == tmp_path.resolve()


def test_a_list_argument_stays_one_argument(tmp_path):
    spaced = tmp_path / "my dir"
    spaced.mkdir()
    command = [sys.executable, "-c", "import sys; print(sys.argv[1])", str(spaced)]
    result = CommandRunner().run(command, tmp_path, 10)
    assert result.code == 0
    assert result.output.strip() == str(spaced)


def test_a_timeout_is_reported_and_the_process_is_stopped(tmp_path):
    result = CommandRunner().run(
        [sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, 0.4
    )
    assert result.timed_out is True
    assert result.code == 124


def test_a_process_without_a_return_code_counts_as_failure(monkeypatch, tmp_path):
    class Proc:
        returncode = None
        pid = 1

        def communicate(self, timeout=None):
            return (None, None)

    monkeypatch.setattr("mutate4c.runner.subprocess.Popen", lambda *args, **kwargs: Proc())
    result = CommandRunner().run("true", tmp_path, 5)
    assert result.code == 1
    assert result.output == ""
    assert result.timed_out is False


def test_verbose_prints_the_command(tmp_path, capsys):
    CommandRunner(verbose=True).run([sys.executable, "-c", "pass"], tmp_path, 10)
    captured = capsys.readouterr()
    assert "python" in captured.err
    assert str(tmp_path) in captured.err


def test_display_command_quotes_a_list():
    assert display_command("make test") == "make test"
    assert display_command(["make", "test"]) == "make test"
    assert "'a b'" in display_command(["echo", "a b"]) or '"a b"' in display_command(
        ["echo", "a b"]
    )
