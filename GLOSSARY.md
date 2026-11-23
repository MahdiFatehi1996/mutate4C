# mutate4C

Mutation testing for C projects: it rewrites one operator or constant at a time, runs the project's tests, and reports whether the tests noticed.

## Language

**Mutation site**:
One operator, boolean, or `0`/`1` constant in the C source that a mutation operator can replace.
_Avoid_: fault, bug

**Mutant**:
The source with exactly one mutation site replaced by its mutant spelling.
_Avoid_: variant, patch

**Killed**:
A mutant whose test run fails or times out.
_Avoid_: caught, detected

**Survived**:
A mutant whose test run still passes; the tests do not distinguish it from the original.
_Avoid_: missed, undetected

**Uncovered**:
A mutation site whose statement anchor line the coverage report does not hit (not merely the site's own line); it is not run.
_Avoid_: skipped, untested

**Test plan**:
The command and working directory used for the baseline and each mutant; the user's `--test-command` overrides project detection.
_Avoid_: test runner, suite

**Mutation snapshot**:
The `.metrics/mutate/<namespace>.edn` file recording killed and survived sites for one C namespace.
_Avoid_: report file, results
