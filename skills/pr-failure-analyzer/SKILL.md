---
name: pr-failure-analyzer
description: "Analyze why a pull request (or a DEPENDS_ON set of PRs) failed CI: fetch the failing runs read-only, classify each failure (FW code / FW wiring / CI-tool / foreign artifact / runner-env / flake / Platform), quote the evidence, name the owner and the next step, match earlier sightings, and grow a shared pitfalls + debug-methods log. Invoked by the Team Portal's PR Monitor 'Triage CI' button and usable by any skill or agent (pr-monitor, bug-triage, fw-ci-expert, fw-pr-watch). Use when: why did my PR fail CI, triage CI failures, why red, analyze failed checks, is this CI failure a flake, have we seen this CI failure before, record a CI pitfall, CI failure lesson."
---

# PR Failure Analyzer

Answers **"why is this PR red, and what do I do about it?"** for one PR or a
cross-repo `DEPENDS_ON` feature set, and remembers what it learned.

- **Read-only.** Never comment, post `/ci`, re-run, cancel, push, edit a PR or
  touch shared infrastructure. Re-triggering stays a human action
  (pr-monitor's `↻ ci` / `pm.py ci`).
- **Knowledge it builds on** (load when relevant, do not duplicate):
  - `fw-ci-knowledge` skill: FW suites, CI tools/workflows, failure modes,
    `check_cross_repo_depends_on.py`.
  - `fw-ci-expert` agent: the classification taxonomy and the
    "earliest cause in the pipeline wins" rule.
  - `bug-triage` skill, *CI-Log Debugging*: the escalation path when the
    failure is a real FW defect that needs a reproduction and root cause.
- **Its own state:**
  - [references/pitfalls.md](references/pitfalls.md): shared pitfalls and
    debug methods. Read it **before** analysing.
  - `pfa.py`: sightings store `~/tasks/pr_failures.db` (`PFA_DB` overrides),
    signature matching and the lesson CLI.

## Procedure

1. **Pin coordinates** per PR: `owner/repo#N`, head SHA, failing check names and
   run URLs. Use `gh pr checks <N> --repo <o/r>` (deduplicated latest-per-context);
   **never** `statusCheckRollup`, which reports superseded `/ci` runs as failures.
2. **Read the failure, failed steps first**:
   `gh run view <run-id> --repo <o/r> --log-failed` (or the GitHub MCP
   `get_job_logs` with `failed_only` and a tail). Widen to
   `gh run view --job <job-id> --log` only for surrounding context. Quote the
   first real error, not the last line (cascades follow the cause).
3. **Look at the change** when the error points into code:
   `gh pr diff <N> --repo <o/r>`, `gh pr view <N> --json body` (the
   `DEPENDS_ON` line names the sibling PRs CI must build together).
4. **Classify** each failing check with the taxonomy below; prefer the
   earliest cause. In a feature set, one root cause often reddens several PRs
   (e.g. a sibling not listed in `DEPENDS_ON`): say so instead of triaging
   each PR separately.
5. **Match history**: `pfa.py match "<signature>"` / `pfa.py recent --repo <r>`.
   A signature seen before with verdict `flake` or `runner-env` is strong
   evidence; cite the earlier sighting.
6. **Answer** with the output contract. Recommend, never perform, the next
   step.
7. **Learn**: when the analysis surfaced a non-obvious pitfall or a reusable
   debug method, propose a *Lesson candidate*. An interactive session (with
   the user's agreement) records it with `pfa.py lesson ...`; automated runs
   only propose it.

## Taxonomy (verdict ids)

| Verdict | Means | Typical evidence | Next step |
|---|---|---|---|
| `fw-code` | The PR's code is wrong | compile/link error, assert, failing unit test in touched code | fix in the PR (`fw-code-implementer`) |
| `fw-wiring` | FW build/test wiring | starfile/CMake/Kconfig/manifest/`DEPENDS_ON` mismatch, missing sibling | fix the wiring or the `DEPENDS_ON` line |
| `ci-tool` | CI tooling misused or broken | workflow/action error before any FW step, lint tool crash | CI owner; re-run once fixed |
| `foreign-artifact` | Another group's artifact regressed | failure inside a consumed artifact version the PR did not change | owning group; pin/bump |
| `runner-env` | Runner/infrastructure | timeout, runner lost, disk full, network/artifact fetch, OOM | re-run `/ci` |
| `flake` | Known intermittent test | same signature passed on retry before; unrelated to the diff | re-run `/ci`, note the flake |
| `platform` | Platform-side CI layer | generic pipeline/merge-queue/platform service failure | Platform team |
| `unknown` | Not enough evidence | logs unavailable / truncated | say what is missing |

## Output contract

Markdown, short, evidence-first:

1. **Verdict** line: overall verdict id + one sentence.
2. Per failing PR/check: `repo#N · check · step`, verdict, **quoted log
   evidence** (≤ 6 lines), file:line when known, owner.
3. **Next step**: concrete and actionable (who, what); say explicitly whether a
   plain `/ci` re-run is worth it.
4. *Earlier sightings* (from `pfa.py`) when any.
5. *Lesson candidate* (optional): title · verdict · symptom · cause · method.

When invoked by the Team Portal, the **last line** must be a single machine
line (no code fence):

```
PORTAL-TRIAGE: {"verdict":"<id>","summary":"<≤120 chars>","owner":"<team/person or ''>","items":[{"repo":"<repo>","pr":<int>,"check":"<name>","run_url":"<url>","signature":"<first error line, ≤200 chars>","verdict":"<id>"}]}
```

`pfa.parse_verdict(answer)` reads it; the portal records each item as a
sighting and adds a one-line verdict note to the PR Monitor feature.

## pfa.py

```bash
P=~/.copilot/skills/pr-failure-analyzer/pfa.py
python3 $P verdicts
python3 $P match "error: 'FOO_REG' undeclared"
python3 $P recent --repo ip.mng.sw.fw --limit 10
python3 $P sight --repo ip.mng.sw.fw --pr 123 --verdict flake --check "unit-test" \
  --signature "<first error line>" --summary "timeout in mctp_test, passed on retry"
python3 $P lesson "Title" --verdict runner-env --symptom "..." --cause "..." --method "..."
```

Module use: `import pfa; pfa.record(...)`, `pfa.match(sig)`,
`pfa.recent([repos])`, `pfa.parse_verdict(text)`, `pfa.fingerprint(text)`.
Signatures are fingerprinted with SHAs, paths, URLs and numbers masked, so the
same error on another run/PR matches.

## Integrations

- **Team Portal** (PR Monitor → feature → `✦ Triage CI`): runs this skill
  headlessly with a read-only profile that has **no shell** (unattended runs
  cannot pass the RTK hook for `gh`): use the `github-mcp-server` read tools
  (`actions_get`/`actions_list`, `get_job_logs` with `failed_only`,
  `pull_request_read`) there. It injects the feature's failing PRs and recent sightings,
  caches the answer per failing-run set, records sightings and the feature
  note. Follow-up questions resume the same session.
- **pr-monitor**: the notes it receives are prefixed `AI CI triage:`.
- **bug-triage**: hand over when the verdict is `fw-code` and the cause is not
  evident from the log (needs a reproduction / bisect).
