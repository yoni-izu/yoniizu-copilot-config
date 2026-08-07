# Speed / bandwidth change — async indication to FW (EP vs SU/C2C)

- **Recorded:** 2026-07-19
- **Status:** Verified from doc (EP databook + controller reference)
- **Tags:** speed change, link speed, bandwidth, lbms, labs, cfg_bw_mgt_int, cfg_link_auto_bw_int, autonomous bandwidth, bandwidth management, directed_speed_change, smlh_ltssm_state, ltssm reason, rasdes, recovery.speed, degraded, interrupt, notification, downstream port, upstream port, endpoint reserved

## Scope / Question
On a link speed change (instructed/directed or degraded/autonomous), is there an ASYNC
(interrupt) indication to local FW — for the PCIe EP and for SU/C2C (DM controller)?

## Findings

### Architected PCIe bandwidth-notification interrupts (both are DSP/RP-only)
The DWC controller has two dedicated SII interrupt sidebands (each has a level `_int` local-app
form and a pulse `_msi` host-MSI form):
| Sideband | Set when | Covers |
|---|---|---|
| `cfg_bw_mgt_int` / `cfg_bw_mgt_msi` | Link Bandwidth Management Status (LBMS) set | **instructed/directed** speed or width change, or retrain to correct unreliable operation |
| `cfg_link_auto_bw_int` / `cfg_link_auto_bw_msi` | Link Autonomous Bandwidth Status (LABS) set | **autonomous / degraded** speed or width change |

**Both are endpoint-reserved.** Reference Table 2-137 (p.1724-1725) says for each, verbatim:
`[EP]: Reserved.` / `[DM,SW]: For upstream port: Reserved.` / `Exists: !(CC_DEVICE_TYPE==CC_EP) ? 1 : 0`.
→ On a pure EP the signals **don't even exist**; on the DM they exist but work **only in the
downstream/RP role**, reserved in the upstream role. Matches PCIe base spec: LBMS/LABS +
LBMIE/LABIE are Downstream-Port features.

### PCIe EP — no dedicated speed-change IRQ
- Directed AND autonomous speed/width changes → **no controller-provided interrupt** (LBMS & LABS
  both reserved/nonexistent on CC_EP).
- Async awareness is indirect:
  1. **LTSSM state-transition monitor** sideband: `smlh_ltssm_state[4:0]`,
     `smlh_ltssm_previous_state`, `smlh_ltssm_state_reason[7:5]` (EP databook p.690 Table 29-11).
     A speed change always transits **Recovery.Speed**; reason encoding distinguishes
     `directed_speed_change` vs `speed_change` (autonomous) (p.697). Continuous status bus — only
     async-to-FW if SoC glue edge-detects it into an IRQ line (integration choice, not a
     controller IRQ).
  2. **RASDES Event Counters** Group #5: `0x0C` Speed Change Gen2, `0x0D` Link Width Change (incl.
     L0p) (p.720) — statistical counters → **poll**, not interrupts.
  3. Link Status register current speed/width → poll.

### SU / C2C (DM dual-mode controller)
- Not CC_EP → sidebands are built. In the **DSP/RP-configured role**: `cfg_bw_mgt_int` (instructed)
  and `cfg_link_auto_bw_int` (degraded/autonomous) are **live, dedicated, level interrupt outputs**
  → genuine async indication to FW.
- In the **USP role**: reserved → same LTSSM-monitor + RASDES/poll fallback as the EP.

### Summary
| | Instructed change | Degraded/autonomous change |
|---|---|---|
| EP | no dedicated IRQ (LBMS reserved) → LTSSM sideband→SoC IRQ, else poll | no dedicated IRQ (LABS reserved) → LTSSM sideband→SoC IRQ, else poll |
| SU/C2C DSP/RP role | `cfg_bw_mgt_int` (level) | `cfg_link_auto_bw_int` (level) |
| SU/C2C USP role | reserved → LTSSM/poll | reserved → LTSSM/poll |

## Sources
- Doc: `~/docs/PCIe EP/controller/DWC_pcie_ctl_reference.pdf` p.1724-1725 (Table 2-137 SII
  Interrupt Signals: `cfg_bw_mgt_int/_msi`, `cfg_link_auto_bw_int/_msi`, EP/USP-reserved,
  `Exists: !(CC_DEVICE_TYPE==CC_EP)`).
- Doc: `~/docs/PCIe EP/controller/DWC_pcie_ctl_ep_databook.pdf` p.690 (Table 29-11 LTSSM state
  transition reason: `smlh_ltssm_state/_previous_state/_state_reason`), p.697
  (`directed_speed_change` vs `speed_change` at Recovery.Speed), p.720 (RASDES EC Group #5:
  0x0C Speed Change Gen2, 0x0D Link Width Change).
- KB cross-ref: `pcie-ep-ip/knowledge/link-down-fw-notification.md`.
