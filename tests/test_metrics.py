from mutate4c.edn import dumps, loads
from mutate4c.metrics import load_history, snapshot_path, write_results
from mutate4c.model import FormResult


def _form(namespace, name, file_name, digest, killed=1, private=False):
    form_id = f"defn-/{name}" if private else f"defn/{name}"
    return FormResult(
        id=form_id,
        namespace=namespace,
        name=name,
        private=private,
        file=file_name,
        line=2,
        end_line=4,
        digest=digest,
        killed=killed,
        survived=0,
        uncovered=1,
        sites=2,
    )


def _viewer_name(form_id):
    """The same split uml-viewer.application.overlay/form-name performs."""

    if form_id.startswith("defn-/"):
        return form_id[6:], True
    if form_id.startswith("defn/"):
        return form_id[5:], False
    return None, False


def test_one_snapshot_per_namespace_preserves_the_other_file(tmp_path):
    write_results(
        tmp_path,
        "a.c",
        [_form("demo", "run", "a.c", "hash-a")],
        {},
    )
    write_results(
        tmp_path,
        "b.c",
        [_form("demo", "stop", "b.c", "hash-b", killed=4)],
        {},
    )
    path = snapshot_path(tmp_path, "demo")
    data = loads(path.read_text(encoding="utf-8"))
    assert data["namespace"] == "demo"
    names = []
    for form in data["forms"]:
        operation, private = _viewer_name(form["id"])
        names.append((operation, private, form["killed"], form["uncovered"], form["sites"]))
    assert ("run", False, 1, 1, 2) in names
    assert ("stop", False, 4, 1, 2) in names
    assert list(tmp_path.joinpath(".metrics", "mutate").rglob("*.edn")) == [path]

    write_results(
        tmp_path,
        "a.c",
        [_form("demo", "run", "a.c", "hash-a", killed=9)],
        {},
    )
    again = loads(path.read_text(encoding="utf-8"))
    by_id = {form["id"]: form for form in again["forms"]}
    assert by_id["defn/run"]["killed"] == 9
    assert by_id["defn/stop"]["killed"] == 4


def test_private_form_id_and_history_round_trip(tmp_path):
    outcome = '["hide.c","demo.core","defn-/hide",8,12,"false","true"]'
    write_results(
        tmp_path,
        "hide.c",
        [_form("demo.core", "hide", "hide.c", "digest", private=True)],
        {outcome: "killed"},
    )
    data = loads(snapshot_path(tmp_path, "demo.core").read_text(encoding="utf-8"))
    assert _viewer_name(data["forms"][0]["id"]) == ("hide", True)
    history = load_history(tmp_path, "hide.c")
    assert history.forms[("demo.core", "defn-/hide")].digest == "digest"
    assert history.outcomes[outcome] == "killed"


def test_snapshot_paths_follow_the_namespace(tmp_path):
    assert snapshot_path(tmp_path, "app.ui.page") == (
        tmp_path / ".metrics" / "mutate" / "app.ui.page.edn"
    )
    assert snapshot_path(tmp_path, "app/ui") == (
        tmp_path / ".metrics" / "mutate" / "app" / "ui.edn"
    )


def test_a_non_canonical_snapshot_is_folded_into_the_namespace_file(tmp_path):
    stale = tmp_path / ".metrics" / "mutate" / "legacy.edn"
    stale.parent.mkdir(parents=True)
    stale.write_text(
        dumps(
            {
                "version": 1,
                "namespace": "demo",
                "source": "b.c",
                "forms": [
                    {
                        "id": "defn/stop",
                        "file": "b.c",
                        "line": 3,
                        "hash": "hash-b",
                        "killed": 4,
                        "survived": 0,
                        "uncovered": 1,
                        "sites": 2,
                    }
                ],
                "outcomes": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    write_results(tmp_path, "a.c", [_form("demo", "run", "a.c", "hash-a")], {})
    assert not stale.exists()
    data = loads(snapshot_path(tmp_path, "demo").read_text(encoding="utf-8"))
    by_id = {form["id"]: form for form in data["forms"]}
    assert by_id["defn/stop"]["killed"] == 4
    assert by_id["defn/run"]["file"] == "a.c"


def test_dropping_a_file_removes_its_namespace_when_nothing_else_remains(tmp_path):
    write_results(tmp_path, "a.c", [_form("demo", "run", "a.c", "hash-a")], {})
    write_results(tmp_path, "a.c", [], {})
    assert not snapshot_path(tmp_path, "demo").exists()
