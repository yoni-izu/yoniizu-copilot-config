# C2C Drop Mode & Isolation Mode (Error Containment) — FW Responsibility

- **Recorded:** 2026-06-22 (updated 2026-06-23 — open gap resolved by Maxim directive; updated 2026-06-28 — RTL register-split validation)
- **Status:** Verified from doc (C2C spec §13.3) + FW-responsibility resolved by spec owner (Maxim, 2026-06-23) + RTL-verified DRAIN-vs-DROP gating (2026-06-28)
- **Tags:** drop mode, isolation mode, viral, error containment, ras, link down, watchdog, completion timeout, fw responsibility, tray reset, scale up reset, reset mask, c2c reset, bmc

## Scope / Question
What is C2C "drop mode", how does it relate to isolation mode/viral, and what (if any)
is the FW responsibility after a device/link enters drop mode? (Triggered by a discussion
on whether FW must reset/retrain or invalidate routing tables.)

## Findings

### Two HW error-containment mechanisms (§13.3)
- **Isolation Mode (§13.3.1):** requestor tracks outstanding requests with a per-request
  **watchdog timer**. On WD expiry the device, when in isolation, can (each item is an
  independent CSR enable):
  - gracefully terminate new outbound requests to the current scale-up port,
  - gracefully terminate new outbound requests to the *other* scale-up ports,
  - discard inbound responses returned to the scale-up port,
  - terminate inbound request traffic toward the current port,
  - provide **dummy Completion-Timeout responses** for all outstanding requests.
  - The dummy CTO responses push originator devices to **idle**, so they can be reused
    **"once management software takes appropriate clean-up actions"** → explicit SW/FW
    *recovery* role. (P1222–P1225)
  - Note: this mechanism also addresses the **link-failure** scenario. (P1229)
  - PoS case: if early write responses are enabled the WD timer is replaced by monitoring
    the **credit-release message**; missing credit release for N cycles → isolation. (P1233)

- **Drop Mode (§13.3.2):** entered on **Viral detection** — viral is either communicated
  from the SoC, or generated on a **C2C link-down** event (incl. the current link). In drop
  mode the link gracefully terminates all further requests issued by the device, and
  *optionally* terminates incoming requests from the C2C link. (P1236–P1237)
  - The link-down event is **propagated to all other C2C links in the device (configurable),
    which also enter drop mode; propagation uses the Viral interface, which crosses dies.**
    (P1238)
  - HW can be configured to handle transactions in drop mode **identically to isolation
    mode**; each per-transaction action can be triggered by either drop or isolation. (P1239)

### Triggers
Three containment triggers exist: **internal viral, external viral, internal isolation.**
Reaction: drop at master pipe, terminate at slave, isolate the response at both — so at the
**source master the traffic is already dropped at the trigger** (no FW action needed to stop
local bleeding).

### IMPORTANT caveat — Viral deprecation
Drop-mode entry *and* its cross-link/cross-die propagation (P1236/P1238) are specified to ride
on the **Viral interface**. If **viral is no longer a valid flow** (per internal decision),
then drop mode's fabric-wide auto-notification loses its transport, leaving **isolation as the
only trigger**. Isolation is per-port / WD-timer based and is *not* inherently broadcast the
way viral-driven drop was → open gap: what now propagates "device lost" across links/dies, and
who updates remote routing tables. **(RESOLVED 2026-06-23 — see below.)**

### RESOLUTION — Maxim directive (2026-06-23, via Akshay)
The spec owner (Maxim) settled the FW-responsibility / propagation question. The viral-based
fabric-wide "device lost" propagation is **not** something FW needs to reconstruct. The agreed
model for **drop mode** is:

- **FW simply reports the link-error event.** That is the entire FW detection-side duty — no
  FW-driven routing-table invalidation, no FW-driven cross-die propagation.
- **The BMC owns recovery and resets the *entire tray*.** Even a **single link error triggers
  a full tray reset** — there is no per-link/partial recovery path FW must orchestrate. The
  tray-wide reset sidesteps the cross-die "device lost" propagation problem entirely.
- **On receiving the "scale up reset" request** (issued by the BMC), FW must, **before issuing
  the reset**:
  1. **configure the reset mask**, and
  2. **enable c2c reset**.
- **Apart from this, there is nothing more to be done for drop mode.**

Implementation references (the c2c-reset / reset-mask configuration on the scale-up-reset path):
- `ip.psoc.sw.fw` PR #390 — https://github.com/Elmnt-Internal/ip.psoc.sw.fw/pull/390
- `module.infra.sw.fw` PR #118 — https://github.com/Elmnt-Internal/module.infra.sw.fw/pull/118

### Lane/Link failure (§13.3.x, P1242)
- Single-lane failure → link-width reduction.
- Full link failure (Link Down) → HW terminates all requests with error → engines halt.
  **"Firmware may choose to reduce the number of active Links before resuming the applications
  on the Accelerator."** → explicit FW recovery role.

### Bottom line on FW responsibility
- **Containment (stop traffic) = HW, automatic.** No FW action to terminate local traffic;
  drop already terminates at master and the source dropped on trigger.
- **Detection-side FW duty = report the link-error event only** (Maxim, 2026-06-23). FW does
  **not** invalidate routing tables or propagate "device lost" across the fabric for drop mode.
- **Recovery = BMC-driven tray reset.** Any link error → BMC resets the **entire tray**. The
  earlier spec text assigning FW post-error cleanup (P1224) and the link-reduction/resume
  decision (P1242) is **superseded for drop mode** by this tray-reset model — FW's only reset
  action is to **configure the reset mask + enable c2c reset before issuing the scale-up reset**
  when the BMC requests it. Nothing more.
- Interrupts FW can hook: **Isolation Mode entrance (SEI)** and **Control Checksum Check
  (SEI, also triggers viral)**; drop/isolation are also exposed as **SPMU/statistics events**.
  (P1113–P1114, P1151–P1166)

### Driver staging
EL C2C and EL SE are brought up **FULL in MANAGEMENT** stage only (not FLA.RT/PRIMEBOOT/P&T).

### RTL: isolation drain register split — DRAIN payload vs DROP arming (verified 2026-06-28)
Verified from `ip.se.rtl` → `c2c_macro/rtl/c2c_axi_master_pipe_isolation.v` (+ slave variant)
how the isolation registers actually compose. There are **two distinct register groups**, and
both are required for the drain to do anything:

1. **DRAIN payload** (`AXI_DRAIN.yml`): `ISOLATION_DRAIN_ENABLE` (0x3c4, the `cfg_drain_en`),
   `ISOLATION_DRAIN_CNTR` (0x3c8, base count), `C2C_ISOLATION_DRAIN_CNT` (0x1b8, iso count),
   `ISOLATION_DRAIN_RESP/RUSER/BUSER` (dummy BRESP/RRESP/user), and `TRANS_PIPE_CFG_2.ISO_DRAIN_CNT_EN`.
   These configure the dummy-completion generator and the count mux — the **payload**, not the trigger.
2. **DROP arming** (`C2C_ISOLATION_DROP`, 0x1bc): per-(trigger × action) enable bits —
   `{int_isolation, int_viral, ext_viral}` × `{outbound_drop, inbound_terminate,
   inbound/outbound_isolate_rsp}`. All `HW_ACCESS=0x0` (SW-owned, reset 0).

RTL gating (the key fact):
```
drain_active = int_isolation_drop_en_r | int_viral_drop_en_r | ext_viral_drop_en_r;
//   each latch = <event> & csr2c2c.c2c_isolation_drop_<trigger>_outbound_drop_en_data_r
fork_ext_sel_rd/wr = ~drain_active_r;   // traffic enters the drain ONLY when drain_active
cfg_drain_cntr_val = (iso_drain_cnt_en & drain_active_ovrd_drain_cfg) ? iso_cnt : base_cnt;
```
So `ISOLATION_DRAIN_ENABLE` arms the dummy-response generator but **routes no traffic**.
Unless `ISOLATION_DROP.*_outbound_drop_en` (master) / `*_inbound_terminate_en` /
`*_inbound_isolate_rsp_en` (slave) are set, `drain_active` is permanently 0 → the drain never
fires and the `use_iso_count`/`iso_timeout` mux (downstream of `drain_active`) is also inert.
This is the RTL realization of the spec's "each item is an independent CSR enable" (§13.3.1).

Also: `int_isolation` (the WD-isolation trigger) is tied to `'0` at the
`c2c_axi_master_pipe_isolation` instantiation in `c2c_axi_master_pipe_top.v` — in this RTL
snapshot only async viral drives the master drop path; internal-isolation triggering is not wired.

### VERIFIED (2026-06-28): isolation-WD drain has no live trigger on this RTL — next-silicon gap
Traced the full production instantiation chain `c2c_top` → `c2c_axi_master_pipe_top` →
`c2c_axi_master_pipe_isolation` on `ip.se.rtl` main. At the leaf instance
(`c2c_axi_master_pipe_top.v`, ~L574-586):
```
.async_ext_viral (async_viral_ext),
.async_int_viral (async_viral_int),
.int_isolation   ('0),              // WD / internal-isolation trigger TIED LOW
```
Consequence, end-to-end:
- `int_isolation_drop_en_r ⇐ ((int_isolation | <drain-feedback>) & iso_outbound_drop_en)`. With
  `int_isolation` tied 0, the only other terms (`rd/wr_drain_activated`, `unexpected_resp`) are
  all products of a drain *already running* — they can **sustain** but never **initiate**.
- The only signals that can *start* a drain are the two async **viral** inputs — i.e. the flow
  the spec owner is **deprecating** (see directive below).
- Therefore, on current silicon RTL, the isolation-WD drain has **no live initiator**. Even a
  "complete" FW (DRAIN payload + `ISOLATION_DROP.int_isolation_outbound_drop_en`) cannot drain on
  isolation. FW isolation-drain config is effectively a **no-op on this RTL**.
- **RTL is frozen** for this rev → this is a **next-silicon** item. Don't expect any isolation-WD
  behavior on current silicon regardless of FW/DTS. Re-verify the `int_isolation('0)` tie-off on
  the next RTL drop before treating the feature as live.
- This is a HW-hookup gap, **not** a FW bug. Keep it out of FW PR comments (nothing FW can do on
  this rev); track it as a HW/RTL dependency with the RTL owner (Sagi) + spec owner (Maxim).

**Review implication:** a FW change that programs only `DRAIN_ENABLE` + counts + resp (and the
DTS that enables it) configures the payload but does not arm isolation — the feature is inert
at runtime with no error. Look for `ISOLATION_DROP` / `MSTR_TERMINATION` / `SLAVE_ISOLATION_TERM`
programming before believing "isolation mode" is functional. (Surfaced reviewing IPSESW-120/121.)
Even with that programming, see the tie-off finding above: no live trigger on this silicon.

### FW programming model — payload preprogrammed, only enable is gated (PR #96, 2026-06-30)
`el_c2c_configure_isolation()` in the SE driver programs the drain-payload registers
**unconditionally** at port init — independent of the DT `isolation-wd-enable` flag:

| Register | Field | Gated by `iso->enable`? |
|----------|-------|--------------------------|
| `ISOLATION_DRAIN_CNTR` (0x3c8) | base count | **No — always written** |
| `C2C_ISOLATION_DRAIN_CNT` (0x1b8) | iso count (write-only, no shadow) | **No** |
| `TRANS_PIPE_CFG_2` (RMW) | `ISO_DRAIN_CNT_EN` (mask `0x100`) | **No** |
| `ISOLATION_DRAIN_RESP` | dummy BRESP/RRESP (2-bit each, masks 0x3 / 0x30) | **No** |
| `ISOLATION_DRAIN_RUSER` / `_BUSER` | dummy user bits | **No** (hardcoded `0` in the EL_C2C_CONFIG macro today) |
| `ISOLATION_DRAIN_ENABLE` (0x3c4, `cfg_drain_en`) | drain block enable | **Yes** — the only write conditional on `iso->enable` |

So the timeout/resp/iso-count values are always live in HW; only the drain block's enable bit
is gated. Functionally harmless because the disabled block does nothing, but the DT binding
contract is easy to misread: properties like `isolation-wd-timeout / -iso-timeout /
-use-iso-count / -bresp / -rresp` are **always applied**, not "only applied when
`isolation-wd-enable` is set".

Implications for reviewers / integrators:
- Don't expect "leaving `isolation-wd-enable=false`" to mean the values are absent from HW —
  they're loaded; only the drain is off.
- Combined with the `int_isolation('0)` tie-off above, an "enabled" config still doesn't arm
  the WD path; only the async viral path can start a drain on this RTL.
- RMW only on `TRANS_PIPE_CFG_2` (it's the only RW register in the set — the rest are
  write-only / one-shot config); the write-only counter is written with its full 32-bit VAL.

## Sources
- Directive: **Maxim** (C2C spec owner), relayed by Akshay Bharad via Teams, 2026-06-23 —
  FW reports link-error event only; BMC resets the entire tray on any link error; on the
  scale-up-reset request FW configures reset mask + enables c2c reset before issuing reset;
  nothing more for drop mode. Impl PRs: `ip.psoc.sw.fw`#390, `module.infra.sw.fw`#118.
- Doc: `~/docs/C2C/PCIe C2C Rev 2.0.0.docx` — §13.3 Error Containment, paragraphs
  P1107–P1114 (interrupts), P1144–P1167 (trace/SPMU events), P1176–P1239 (RAS / Isolation /
  Drop), P1242 (Lane/Link failure).
- RTL/FW: `ip.se.sw.fw` not present in local workspace as of 2026-06-22 (no local drop/
  isolation/viral handling found).
