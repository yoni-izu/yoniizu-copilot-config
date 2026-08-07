# Debugging Memoir

Persistent notes on debugging style, failure signatures, and root-cause patterns for the HSI FW
codebase. Grows with every investigation — captures symptom→root-cause mappings, "always check"
items, and environment traps so recurring bugs are recognized instantly.

Read this in full before every investigation (Step 0). Fold new, reusable lessons back in after
(Step 9) — consolidate under the right theme; keep entries tight and principle-first.

---

## Debugging Philosophy

- **Reproduce before theorizing.** A hypothesis you can't reproduce is a guess. If it won't
  reproduce, that non-determinism is itself the clue (race, uninit state, env dependence).
- **Verify against the code path, not the symptom.** Read callers, init order, and the state the
  failing line assumes — not just the crashing line.
- **Regression vs latent is the first fork.** A regression → bisect the manifest/commit history. A
  latent/new bug → reason from the code and the spec. Don't bisect a bug that never worked.
- **Label every finding.** *Confirmed* (reproduced / reverted-and-proved), *likely* (strong
  evidence, not proven), *unverified* (hypothesis). Never present a guess as a fact.
- **Check the spec before blaming HW code.** The register field semantics (RW1C, RO, sticky, reset
  value) frequently flip the conclusion — pull them from the IP skill first.
- **Confirm the root cause by reverting it.** The gold standard is: revert the suspected change on
  the bad build and watch the failure vanish. Correlation of a commit in a bisect is not yet proof.
- **Isolation is non-negotiable.** Never probe/edit/bisect in the user's active dev workspace or over
  a dirty tree — in-progress work is sacred.

## Always Check

- **Init order & stage.** Which boot stage (FLA.RT / PRIMEBOOT / MANAGEMENT / POWER&THERMAL) owns the
  code, and whether the failing code assumes state a *later* stage sets up.
- **Reset/value assumptions.** Does the code assume a register/reset value that the current variant or
  reset domain doesn't guarantee?
- **Cross-repo skew.** A regression with no local code change is often a *dependency* repo moving —
  diff the manifest, not just the app repo.
- **Env vs code.** Same source, different result across sim/palladium/onix/asic → environment,
  timing, or uninitialized-memory dependence, not a source bug.
- **Known-issue first.** Search Jira (incl. recently-closed) before a deep dive — a re-opened or
  fixed-then-broken bug shortcuts the whole investigation.

## Failure Signatures

_Symptom → root cause → fix. Grouped by theme/IP. Add entries as they're confirmed._

### Boot / Reset / Init
_(none yet)_

### PCIe EP / PHY
_(none yet)_

### Scale-Up (C2C) / D2D
_(none yet)_

### RAS / Manageability
_(none yet)_

### Cross-IP / Project-Level
_(none yet)_

## Environment & Tooling Traps

### Manifest Bisection
- Manifest `version=` is timestamped (`<semver>-<DDMMYYYY>-<HHMMSS>`) — **sort by the timestamp**,
  not lexically by semver, to order candidates for the binary search.
- A good→bad manifest step usually moves exactly one repo's `revision`; diff the manifests to find it
  before falling back to a per-repo `git bisect`.

### Build / Emulation
_(none yet)_

### CI Logs
- CI runs **only** on a `/ci` comment (not on open/push); close/reopen does not re-trigger it.
- `gh run view <run-id> --log-failed` is the fast path — read failed steps first, widen only for context.
- Stale check-run trap: `statusCheckRollup` counts superseded runs on the same SHA. Trust
  `gh pr checks` (latest-per-context) or `gh run list -b <branch> -L1` for the true state.
- A failure that reproduces with `build-fw` locally is a source bug; one that only fails in CI points at
  manifest/env skew → bisect the manifest, not the source.
