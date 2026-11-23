from mutate4c.coverage import covered_lines


def _lcov(tmp_path, relative: str, text: str):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _source(tmp_path, relative: str, text: str = "int f(void) { return 0; }\n"):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_lcov_marks_executed_lines(tmp_path):
    _lcov(
        tmp_path,
        "target/coverage/c/lcov.info",
        "SF:src/demo/app.c\nDA:1,3\nDA:2,0\nend_of_record\n",
    )
    source = _source(tmp_path, "src/demo/app.c")
    assert covered_lines(tmp_path, source) == {1}


def test_lcov_merges_both_coverage_directories(tmp_path):
    _lcov(tmp_path, "target/coverage/c/lcov.info", "SF:src/a.c\nDA:1,1\nend_of_record\n")
    _lcov(tmp_path, "coverage/lcov.info", "SF:src/b.c\nDA:2,0\nend_of_record\n")
    assert covered_lines(tmp_path, _source(tmp_path, "src/a.c")) == {1}
    assert covered_lines(tmp_path, _source(tmp_path, "src/b.c")) == set()


def test_a_missing_report_has_no_coverage(tmp_path):
    source = _source(tmp_path, "src/demo/app.c")
    assert covered_lines(tmp_path, source) is None


def test_a_file_absent_from_the_report_has_no_coverage(tmp_path):
    _lcov(tmp_path, "coverage/lcov.info", "SF:src/other.c\nDA:1,1\nend_of_record\n")
    assert covered_lines(tmp_path, _source(tmp_path, "src/demo/app.c")) is None


def test_lcov_accepts_checksums_and_repeated_hits(tmp_path):
    _lcov(
        tmp_path,
        "target/coverage/c/lcov.info",
        "SF:src/demo/app.c\nDA:9,3,checksum\nDA:10,0\nend_of_record\n",
    )
    source = _source(tmp_path, "src/demo/app.c")
    assert covered_lines(tmp_path, source) == {9}


def test_lcov_keeps_a_file_that_has_no_end_record(tmp_path):
    _lcov(
        tmp_path,
        "target/coverage/c/lcov.info",
        "SF:src/a.c\nDA:1,1\nSF:src/b.c\nDA:2,0\n",
    )
    assert covered_lines(tmp_path, _source(tmp_path, "src/a.c")) == {1}
    assert covered_lines(tmp_path, _source(tmp_path, "src/b.c")) == set()


def test_lcov_does_not_let_a_shorter_path_steal_hits(tmp_path):
    _lcov(
        tmp_path,
        "target/coverage/c/lcov.info",
        "SF:b/app.c\nDA:2,1\nend_of_record\n"
        "SF:a/b/app.c\nDA:1,1\nend_of_record\n",
    )
    short = _source(tmp_path, "b/app.c")
    long = _source(tmp_path, "a/b/app.c")
    assert covered_lines(tmp_path, long) == {1}
    assert covered_lines(tmp_path, short) == {2}


def test_a_report_keyed_by_a_longer_path_matches(tmp_path):
    _lcov(
        tmp_path,
        "target/coverage/c/lcov.info",
        "SF:/build/proj/src/demo/app.c\nDA:4,1\nend_of_record\n",
    )
    source = _source(tmp_path, "src/demo/app.c")
    assert covered_lines(tmp_path, source) == {4}


def test_an_absolute_report_key_matches_a_project_relative_source(tmp_path):
    source = _source(tmp_path, "src/demo/app.c")
    _lcov(
        tmp_path,
        "coverage/lcov.info",
        f"SF:{source.resolve().as_posix()}\nDA:5,1\nend_of_record\n",
    )
    assert covered_lines(tmp_path, source) == {5}


def test_a_report_key_with_src_removed_matches(tmp_path):
    source = _source(tmp_path, "src/demo/app.c")
    stripped = source.resolve().as_posix().replace("/src/", "/")
    _lcov(
        tmp_path,
        "coverage/lcov.info",
        f"SF:{stripped}\nDA:6,1\nend_of_record\n",
    )
    assert covered_lines(tmp_path, source) == {6}


def test_headers_use_the_same_coverage_report(tmp_path):
    _lcov(tmp_path, "target/coverage/c/lcov.info", "SF:src/demo/api.h\nDA:3,1\nend_of_record\n")
    source = _source(tmp_path, "src/demo/api.h", "static inline int f(void) { return 0; }\n")
    assert covered_lines(tmp_path, source) == {3}
