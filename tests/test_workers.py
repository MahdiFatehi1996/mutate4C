import errno
from pathlib import Path

import pytest

from mutate4c.workers import create_workers, delete_tree, new_run_dir, worker_count


def test_delete_tree_retries_when_a_directory_gains_a_file(tmp_path, monkeypatch):
    tree = tmp_path / "worker"
    tree.mkdir()
    (tree / "old-report.xml").write_text("done", encoding="utf-8")
    injected = {"count": 0}
    original_rmdir = Path.rmdir

    def rmdir(self):
        if self == tree and injected["count"] == 0:
            injected["count"] += 1
            (self / "late-report.xml").write_text("flushed", encoding="utf-8")
            raise OSError(errno.ENOTEMPTY, "Directory not empty")
        return original_rmdir(self)

    monkeypatch.setattr(Path, "rmdir", rmdir)
    delete_tree(tree, attempts=3, sleep=lambda _: None)
    assert not tree.exists()
    assert injected["count"] == 1


def test_delete_tree_tolerates_a_file_that_vanishes(tmp_path, monkeypatch):
    tree = tmp_path / "worker"
    tree.mkdir()
    vanishing = tree / "report.xml"
    vanishing.write_text("temp", encoding="utf-8")
    original_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self == vanishing and vanishing.exists():
            original_unlink(self)
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    delete_tree(tree, attempts=1, sleep=lambda _: None)
    assert not tree.exists()


def test_delete_tree_ignores_a_missing_path(tmp_path):
    missing = tmp_path / "already-gone"
    delete_tree(missing, attempts=1, sleep=lambda _: None)
    assert not missing.exists()


def test_delete_tree_removes_a_regular_file(tmp_path):
    path = tmp_path / "worker-file"
    path.write_text("x", encoding="utf-8")
    delete_tree(path, attempts=1, sleep=lambda _: None)
    assert not path.exists()


def test_delete_tree_unlinks_a_symlink_without_following_it(tmp_path):
    real = tmp_path / "project"
    real.mkdir()
    kept = real / "important.txt"
    kept.write_text("keep", encoding="utf-8")
    tree = tmp_path / "worker"
    tree.mkdir()
    try:
        (tree / "src").symlink_to(real)
        link_root = tmp_path / "worker-link"
        link_root.symlink_to(real)
    except OSError:
        pytest.skip("this platform does not permit symlinks")
    delete_tree(tree, attempts=1, sleep=lambda _: None)
    delete_tree(link_root, attempts=1, sleep=lambda _: None)
    assert not tree.exists()
    assert not link_root.exists()
    assert kept.read_text(encoding="utf-8") == "keep"


def test_delete_tree_reraises_after_the_attempts_are_exhausted(tmp_path, monkeypatch):
    tree = tmp_path / "worker"
    tree.mkdir()
    (tree / "stuck.txt").write_text("x", encoding="utf-8")

    def rmdir(self):
        raise OSError(errno.ENOTEMPTY, "Directory not empty")

    monkeypatch.setattr(Path, "rmdir", rmdir)
    sleeps: list[float] = []
    with pytest.raises(OSError) as caught:
        delete_tree(tree, attempts=3, sleep=sleeps.append)
    assert caught.value.errno == errno.ENOTEMPTY
    assert sleeps == [0.25, 0.5]
    assert tree.exists()


def test_workers_keep_a_private_copy_and_link_the_rest(tmp_path):
    source = tmp_path / "src" / "demo.c"
    source.parent.mkdir()
    source.write_text("int place(int x) { return x > 0; }\n", encoding="utf-8")
    sibling = tmp_path / "src" / "other.c"
    sibling.write_text("int kept(void) { return 1; }\n", encoding="utf-8")
    marker = tmp_path / "tests" / "keep.txt"
    marker.parent.mkdir()
    marker.write_text("keep", encoding="utf-8")
    (tmp_path / "CMakeLists.txt").write_text("project(demo C)\n", encoding="utf-8")
    original = source.read_bytes()

    base = new_run_dir(tmp_path)
    try:
        workers = create_workers(base, tmp_path, "src/demo.c", original, 2)
        assert len(workers) == 2
        for worker in workers:
            private = worker / "src" / "demo.c"
            assert private.is_file()
            assert not private.is_symlink()
            assert private.read_bytes() == original
            assert (worker / "src" / "other.c").read_text(encoding="utf-8") == sibling.read_text(
                encoding="utf-8"
            )
            assert (worker / "tests" / "keep.txt").read_text(encoding="utf-8") == "keep"
            config = worker / "CMakeLists.txt"
            assert config.is_file()
            assert not config.is_symlink()
        (workers[0] / "src" / "demo.c").write_text("changed\n", encoding="utf-8")
        assert workers[1].joinpath("src/demo.c").read_bytes() == original
        assert source.read_bytes() == original
    finally:
        delete_tree(base)

    assert source.read_bytes() == original
    assert sibling.read_text(encoding="utf-8") == "int kept(void) { return 1; }\n"
    assert marker.read_text(encoding="utf-8") == "keep"
    assert not base.exists()


def test_worker_count_follows_sites_cores_and_the_requested_cap():
    import os

    cores = os.cpu_count() or 1
    assert worker_count(10, None) == min(10, cores)
    assert worker_count(10, 3) == min(3, cores)
    assert worker_count(2, 8) == min(2, cores)
