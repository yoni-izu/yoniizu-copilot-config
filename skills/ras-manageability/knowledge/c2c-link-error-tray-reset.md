# C2C / Scale-Up link errors → tray reset (FW vs BMC split)

- **Recorded:** 2026-06-23
- **Status:** Verified from spec-owner directive (Maxim, via Akshay) + landed FW PRs
- **Tags:** c2c, scale-up, link error, drop mode, viral, tray reset, scale up reset, reset mask, c2c reset, bmc, fw responsibility, spec owner directive, maxim

## Scope / Question
On a C2C / Scale-Up link error (drop mode entry, link-down, etc.), what is FW's responsibility
versus the BMC's? Specifically, does FW have to invalidate routing tables, propagate the
"device lost" event across dies, or drive any retrain/recovery?

## Findings

Spec-owner directive (Maxim, via Akshay, 2026-06-23) settles the FW/BMC split for **drop mode**
and any C2C link-error recovery:

- **FW only reports the link-error event.** No FW-driven routing-table invalidation, no FW
  cross-die "device lost" propagation. Viral is no longer a valid flow, so the spec's earlier
  viral-based fabric-wide auto-notification is moot — but nothing FW needs to reconstruct.
- **The BMC owns recovery and resets the *entire tray*.** Even a **single link error triggers
  a full tray reset** — there is no per-link/partial recovery path FW must orchestrate. The
  tray-wide reset sidesteps the cross-die "device lost" propagation problem entirely.
- **On receiving the "scale up reset" request** (issued by the BMC), FW must, **before
  issuing the reset**:
  1. **configure the reset mask**, and
  2. **enable c2c reset**.
- **Apart from this, there is nothing more to be done for drop mode.** No FW-driven link
  reduction, no resume-after-cleanup orchestration on the link-error path.
- HW containment (stopping local traffic on a drop event) is automatic — drop already
  terminates at master and the source dropped on trigger. No FW action to "stop bleeding"
  locally.
- Interrupts FW *may* hook: **Isolation Mode entrance (SEI)** and **Control Checksum Check
  (SEI, also triggers viral)**. Drop/isolation are also exposed as **SPMU/statistics events**.

This **supersedes** the earlier spec text that assigned FW post-error cleanup (C2C spec P1224)
and the link-reduction/resume decision (P1242). For drop mode, FW's only reset action is the
mask + c2c-reset-enable step above when the BMC requests the scale-up reset.

## Sources
- Directive: **Maxim** (C2C spec owner), relayed by Akshay Bharad via Teams, 2026-06-23
- FW impl (reset-mask + c2c-reset-enable on scale-up reset path):
  - `Elmnt-Internal/ip.psoc.sw.fw`#390 — https://github.com/Elmnt-Internal/ip.psoc.sw.fw/pull/390
  - `Elmnt-Internal/module.infra.sw.fw`#118 — https://github.com/Elmnt-Internal/module.infra.sw.fw/pull/118
- Spec context (now partially superseded): `~/docs/C2C/PCIe C2C Rev 2.0.0.docx` §13.3 Error
  Containment, P1107–P1114 (interrupts), P1144–P1167 (trace/SPMU events), P1176–P1239
  (RAS / Isolation / Drop), P1242 (Lane/Link failure)
- Related: `su-ip` knowledge base — `drop-mode-error-containment.md` (full C2C-side detail,
  including RTL gating and the `int_isolation('0)` tie-off)
