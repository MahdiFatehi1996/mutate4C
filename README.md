# mutate4C

Mutation testing for C. One run finds the mutation sites in your source,
rewrites one operator or constant at a time, runs your tests against each
mutant, and writes the snapshot [uml-viewer](https://github.com/unclebob/uml-viewer)
already reads.

This is a C-only fork of [unclebob/mutator](https://github.com/unclebob/mutator).
Function names, namespaces, line spans, and byte spans come from
[crap4C](../crap4C), so `.metrics/mutate` joins `.metrics/crap.edn` on the
same keys.

A mutant is killed when the tests fail or time out. It survives when the
tests still pass. A site on a line the coverage report does not hit is
uncovered and is not run. The score uml-viewer paints is
killed / (killed + survived).

## Run

```bash
./mutate4c
```

crap4C has to be checked out next to this directory (`../crap4C`), because
that is where the function names and namespaces come from. From a project
root the tool walks the tree, skips `test`, `spec`, `build`, `target`,
`coverage`, `CMakeFiles`, and `cmake-build-*`, and writes
`.metrics/mutate/<namespace>.edn`.

The first run of a tree executes every covered mutant. Start with the file
you are working on.

```bash
./mutate4c src/demo/core.c          # one file, or a directory
./mutate4c --scan src/demo          # list sites, do not run tests
./mutate4c --changed                # git additions and edits
./mutate4c --mutate-all             # rerun killed mutants too
./mutate4c --no-coverage            # do not skip uncovered lines
./mutate4c --reuse-coverage         # read reports already on disk
./mutate4c --test-command "make check"
./mutate4c --lines 12,13 src/demo/core.c
./mutate4c --max-workers 3 src/demo/core.c
```

Once a snapshot exists, the next run is differential. It reruns survivors
and every site in a function whose text changed. Killed mutants in an
unchanged function stay killed. `--since-last-run` is that same selection.
`--mutate-all` ignores it.

Exit `0` when every executed mutant was killed. Exit `2` when the baseline
tests fail. Exit `3` when a mutant survives. A failed baseline does not
rewrite the snapshot.

## Workers

Mutants of one file run at the same time. The default is one worker per core.
`--max-workers` sets the cap. The run uses the smaller of that cap, the
number of cores, and the number of selected sites. Files are still taken one
at a time.

Each worker is a directory under `target/mutation-workers`. The file being
mutated is a private copy. The rest of the tree is linked (or copied where a
link is not permitted). The original files stay as they are. The overlay is
removed when that file's mutants finish.

The baseline still runs once, in the real tree, before any worker starts.
The mutant command then runs inside the worker, in the same directory it
would have used in the real tree. A copy left under `target/mutator-backup/`
by an interrupted older run is restored before a non-scan run.

## Snapshot

uml-viewer loads every `*.edn` file under `.metrics/mutate`. Each file is one
namespace. A C namespace is the dotted source path with `src/` and the
extension removed, so `src/demo/board.c` is `demo.board`.

```clojure
{:version 1
 :source "src/demo/board.c"
 :namespace "demo.board"
 :outcomes {"[\"src/demo/board.c\",\"demo.board\",\"defn/place\",10,11,\">\",\">=\"]" :survived}
 :forms [{:id "defn/place"
          :kind "defn"
          :file "src/demo/board.c"
          :line 2
          :end-line 4
          :hash "…"
          :killed 1
          :survived 1
          :uncovered 0
          :sites 2}
         {:id "defn-/hide"
          :kind "defn-"
          :killed 0
          :survived 0
          :uncovered 1
          :sites 1}]}
```

`:sites` is every mutation in the function, including uncovered ones. A
function with internal linkage (`static`) uses `defn-/`, the private form
id uml-viewer understands. The viewer prints `---no mutation sites---` when
that count is zero.

## What C mutates

Tokens inside comments, strings, character literals, and macro bodies are
left alone. A replacement that does not compile fails the test run and counts
as killed.

| Mutation | Examples |
| --- | --- |
| arithmetic | `+`↔`-`, `*`↔`/` |
| comparison | `>`↔`>=`, `<`↔`<=` |
| equality | `==`↔`!=` |
| logical | `&&`↔`||` |
| boolean | `true`↔`false` |
| constant | `0`↔`1` |
| unary | delete `!`, delete unary `-` |

A `*` or `&` that dereferences a pointer or takes an address is a
`pointer_expression`, not a unary operator, so it is never a site. Bitwise
and shift operators are not mutated. `0x0` is not the literal `0` and is not
mutated.

## Tests and coverage

The baseline command has to pass before any mutant runs. A mutant's timeout
is ten times that baseline, and at least two seconds. `--timeout-factor`
changes the multiple. `--test-command` replaces the default.

Default test plan, detected at the project root:

| Project file | Command |
| --- | --- |
| `CMakeLists.txt` | `ctest --output-on-failure` |
| `Makefile` | `make test` |
| none | a command that fails |

Coverage is LCOV from `target/coverage/**/lcov.info` and
`coverage/**/lcov.info`. When the user does not supply `--coverage-command`,
`gcovr --lcov --output target/coverage/c/lcov.info` produces the report at
the project root. `--reuse-coverage` reads what is already on disk. A file
missing from the report is treated as uncovered, and the run says so.

## Development

```bash
PYTHONPATH=src:../crap4C/src python -m pytest
```

The 0BSD license and Uncle Bob's original attribution are in `LICENSE`.
