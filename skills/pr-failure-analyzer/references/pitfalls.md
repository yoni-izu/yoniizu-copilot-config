# PR CI failures: pitfalls and debug methods

Shared, technical knowledge only (no personal data). Read before analysing a
failure. Append lessons with `pfa.py lesson ...`; edit by hand to merge or
correct entries.

## Standing pitfalls

### CI only runs on a `/ci` comment
- **Symptom**: a PR shows "not run" or old results after a push.
- **Cause**: CI here fires on a `/ci` comment, never on push, open or reopen.
- **Method / fix**: ask the author to post `/ci`; never post it without approval.
  (bug-triage *CI-Log Debugging*; pr-monitor `pm.py ci`.)

### Superseded runs look like failures in `statusCheckRollup`
- **Symptom**: the rollup says FAILURE while the latest run is green.
- **Cause**: every `/ci` on the same SHA adds runs; the rollup keeps superseded ones.
- **Method / fix**: trust `gh pr checks` (latest per context) or the newest
  `gh run list -b <branch> -L1`. (pr-monitor `probe()` uses `gh pr checks` for this reason.)

### One root cause, many red PRs
- **Symptom**: several PRs in a feature set fail in the same build/test step.
- **Cause**: cross-repo change built without its sibling (missing or wrong
  `DEPENDS_ON` line) or a shared foreign artifact regression.
- **Method / fix**: compare the failing step across the set first; check each
  PR body's `DEPENDS_ON` line (`fw-ci-knowledge` has
  `check_cross_repo_depends_on.py`).

### Cascading errors hide the cause
- **Symptom**: the end of the log shows generic "exit 1" / many follow-on errors.
- **Cause**: build systems keep going or report late.
- **Method / fix**: read `--log-failed` from the top of the failing step and
  quote the **first** error.

### Passes locally, fails in CI
- **Symptom**: the same build is green on a workspace.
- **Cause**: CI builds a pinned manifest; the local workspace has other revisions.
- **Method / fix**: treat as manifest/dependency skew; compare the CI manifest
  pins; bisect per repo (bug-triage *Manifest Bisection*).

## Lessons
