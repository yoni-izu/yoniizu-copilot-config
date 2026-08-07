# Link-down FW notification — HW interrupt hooks (abrupt & graceful)

- **Recorded:** 2026-07-19
- **Status:** Verified from doc (EP databook) + cross-referenced SU/C2C KB
- **Tags:** link down, link_req_rst_not, smlh_req_rst_not, radm_link_down, hot reset, dl_down, perst, sei, viral, drop mode, isolation, interrupt, notification, containment, abrupt, graceful

## Scope / Question
If the PCIe/C2C link goes down (abruptly or gracefully), does the HW interrupt FW — i.e.
are the notification hooks in place — for both the EP and the Scale-Up (SU/C2C) controller?

## Findings

### Key framing
HW does the **containment autonomously** (flush/quiesce on EP; drop-at-master / terminate-at-slave
/ isolate-response on C2C). The interrupt/notification exists so FW can **report/recover**, NOT to
stop the bleeding. If FW is slow to service it, the link is already flushed/dropped by HW.

### PCIe EP (DWC EP controller v6.42a-lca01) — HW is in place
- Reset-request sideband pair `smlh_req_rst_not` → `link_req_rst_not`. The reset event source is
  literally *"From link_down event or remote partner's TS hot-reset"* (databook p.105).
- Databook p.108 (Fig 2-30) verbatim: *"Early warning that reset is imminent. **You can use this to
  create an interrupt** to your application or software; informing it that the link has gone down
  and that it should stop data transmission."* → the hookable link-down IRQ.
- **Abrupt/surprise down** (LTSSM L0→Recovery→Detect→DL_Down): `radm_link_down` pulses,
  `link_req_rst_not` asserts.
- **Graceful down** (host Hot Reset / Secondary Bus Reset, orderly L2/L3 power-down, PERST#
  assertion): earlier two-step warning (`smlh_req_rst_not` first) → time to flush/quiesce; PERST#
  is a pin with its own ISR (deferred-PERSTn path, see app-ctrl-crs-drs.md).
- Cause is readable: LTSSM-state sideband + RASDES EC group#1 cause codes (p.694: `8h`=remote
  requested, `Ah`=hot reset, `9h`=width change, `Bh`=disable). DL_Down→DL_Up / DRS exposed (p.611).

### Scale-Up (SU / C2C, DM controller) — HW is in place
- Same controller core → same `link_req_rst_not` / `radm_link_down` sidebands.
- **Plus** C2C-specific: **Isolation Mode entrance raises an SEI**; a **C2C link-down generates
  viral → Drop Mode**; drop/isolation also surfaced as **SPMU/statistics events**
  (see su-ip drop-mode-error-containment.md).
- Per C2C spec owner (Maxim): on link error FW's only duty is to **report the event**; BMC resets
  the whole tray. So HW interrupts FW, but recovery is BMC-driven, not an FW retrain loop.

### Summary table
| | Abrupt down | Graceful down |
|---|---|---|
| EP | radm_link_down + link_req_rst_not (hookable IRQ) | two-step smlh/link_req_rst_not warning + PERST# ISR |
| SU/C2C | viral→drop + SEI/SPMU event | isolation-WD (CTO)→SEI + link_req_rst_not |

## Sources
- Doc: `~/docs/PCIe EP/controller/DWC_pcie_ctl_ep_databook.pdf` p.105 (reset-event source),
  p.107-108 (Fig 2-30, "create an interrupt … link has gone down"), p.611 (DRS on DL_Down→DL_Up),
  p.694 (RASDES EC group#1 link-down cause codes).
- KB cross-ref: `pcie-ep-ip/knowledge/app-ctrl-crs-drs.md` (PERSTn ISR, DL_Down→DL_Up),
  `su-ip/knowledge/drop-mode-error-containment.md` (SEI on isolation entrance, viral→drop on
  C2C link-down, SPMU events, BMC tray-reset recovery model).
