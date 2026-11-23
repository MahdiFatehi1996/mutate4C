# Test plan and coverage are detected per C project

The test plan follows the upstream per-language pattern: a CMake project runs `ctest --output-on-failure`, a Make project runs `make test`, and anything else falls back to a command that fails, so every mutant is killed until the user supplies `--test-command`. Coverage comes from LCOV under `target/coverage`, produced by `gcovr` when the user does not supply `--coverage-command`.

Consequences: projects with custom harnesses must pass `--test-command`; a missing coverage report makes every site uncovered rather than guessed.
