# PR Set Review — IPD2DSW-125
- Scope: local workspace review (T-068, 4 linked PRs)
- PRs:
  - Elmnt-Internal/ip.d2d.sw.fw#72
  - Elmnt-Internal/ip.psoc.sw.fw#374
  - Elmnt-Internal/project.iridium.sw.fw.psoc_primeboot#428
  - Elmnt-Internal/module.infra.sw.fw#85
- Heads reviewed:
  - ip.d2d.sw.fw: 67a2661e7ef3a305a4d8b741d32965fdeccd02ff
  - ip.psoc.sw.fw: fe30b60cc0b49043950773671057d87a96f9de23
  - project.iridium.sw.fw.psoc_primeboot: 4b8527efc833c9372cd30c5e2d62b38651cb12da
  - module.infra.sw.fw: fae987885c195a4a13c0e1602c94b69703cf05b7
- Drafted: 2026-06-11 19:19

## Recommended verdict: request-changes
One correctness issue in transport-selector handling can clobber global access mode outside this flow.

## Findings
### [BLOCKER] ip/d2d/drivers/el_d2d_manager.c:979
`el_d2d_mgr_load_fw_and_linkup()` forcibly restores transport to `EL_DIE_TRANSPORT_GATEWAY` after the MMIO phase, rather than restoring the previous selector value.

Current pattern:
- `el_die_set_transport(EL_DIE_TRANSPORT_MMIO);`
- run steps
- `el_die_set_transport(EL_DIE_TRANSPORT_GATEWAY);`

Why this is risky:
- Selector is process-global in module.infra (`active_sel`), not local to D2D manager.
- If any caller set selector to MMIO (or another backend in future) before entering this path, this function silently overrides global state after return.
- Behavior becomes order-dependent and hard to debug in reset/re-init sequences.

Suggested fix:
- Save previous selector at entry (`prev = el_die_get_transport_sel()`), set MMIO for this phase, then always restore `prev` before return.
- Keep current comments about single-threaded boot, but make function locally state-preserving.

Proposed comment body:
"`el_d2d_mgr_load_fw_and_linkup()` currently sets transport to MMIO and then unconditionally sets it back to GATEWAY. Since `el_die` transport is process-global, this can clobber a pre-existing selector chosen by another boot/reset path. Please save the previous selector (`el_die_get_transport_sel()`) and restore that value on exit instead of forcing GATEWAY."

### [SUGGESTION] include/el_die_access.h:102
Docs and enum naming are mostly clear, but the public comments still tie the gateway backend to one concrete transport name (`SPI gateway`) in shared API text.

Why this matters:
- This interface is now explicitly runtime-switchable and shared.
- Naming one specific transport in top-level API comments hardcodes current integration assumptions into reusable docs.

Suggested tweak:
- Keep implementation-specific detail in backend docs/driver docs, but use generic wording in shared API comments (e.g. `indirect gateway backend`).

Proposed comment body:
"Nice progress on runtime selector support. Small doc cleanup: in shared `el_die_access.h` API comments, prefer generic wording for the gateway backend (e.g. `indirect gateway backend`) rather than transport-specific naming. Keeps the interface text backend-agnostic as this grows."

## Dropped / considered
- Primeboot board config cleanup (`CONFIG_EL_*` removals and overlay node churn): no concrete correctness regression found from diff alone; mostly pruning/noise reduction.
- D2D manager refactor into step helpers: shape improved readability; no clear resource-leak/error-path regression identified in reviewed paths.
- Embedded FW blob gating: verified now gated by `CONFIG_EL_D2D_MCU_FW_EMBEDDED` in CMake and call-sites.
- Pending GitHub draft comments by reviewer: none found on these PRs (safe to draft fresh comments).
