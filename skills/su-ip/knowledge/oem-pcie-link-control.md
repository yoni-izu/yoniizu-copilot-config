# OEM PCIe Link Control and Root-Port Link Disable

- **Recorded:** 2026-10-25
- **Status:** Verified from working manageability spec and DM register reference; initial API published; auto-flush integration gap under investigation
- **Tags:** link control, link disable, root port, ownership, quiesce

## Findings

- The current Device Manageability Specification names the command
  `OemPcieLinkControl (0x51)` (formerly `OemLinkControl`), section 9.5.3.1.3.9.
  Subcommands are Link down (1), Link up (2), Get status (3).
- Down/up require MC ownership and are non-blocking; success means accepted,
  not completed. Status is read-only and ownership-free. Already-held down
  and already-up requests are idempotent. Concurrent transitions return
  `OEM_LINK_BUSY`. Down must reject active device-side traffic with
  `OEM_LINK_TRAFFIC_ACTIVE`. The MC is responsible for stopping workloads.
- Status distinguishes held-down, training, up, down-not-held, transitioning,
  and includes `heldByMc`, LTSSM, negotiated width and speed. Ownership release
  does not change the link state; chip reset clears the held-down state.
- The command covers both EP and SU interfaces, but the user-selected initial
  SE mechanism is Root-Port-only: set/clear standard Link Control bit 4,
  `PCIE_CAP_LINK_DISABLE`. The DWC reference says this directs LTSSM to
  Disabled and is reserved on EPs and switch upstream ports. Bit readback
  changes immediately regardless of actual link state; it cannot prove
  transition completion. Clearing requires respecting PCIe reset timing
  before the first configuration read. Crosslink can gate writes.
- A raw bit-toggle API is only the hardware primitive, not the full OEM
  contract. Quiesce enforcement, transition serialization and held/status
  reporting need explicit layer ownership when implementing.

## Sources

- `Device Manageability Specification.docx`, section 9.5.3.1.3.9,
  tables 9-72 through 9-80. Effective XML paragraph indices (all body
  descendants, inserted text included, deleted text excluded):
  6084-6118, 6130-6171, 6182-6201, 6207-6287, 6293-6319.
- `DWC_pcie_ctl_reference.pdf`, PDF page 2176, bit 4 of
  `LINK_CONTROL_LINK_STATUS_REG`.
- Related RAS knowledge: `ras-manageability/knowledge/oem-pcie-link-control.md`.

## Initial SE API implementation (2026-10-25, PR #272 / 89cbb07)

In the A1 power-on workspace, `el_se_set_port_link_enabled_flat()` provides
non-blocking RP-only enable/disable, requiring the DM device's MC-owned flag.
`el_se_get_port_link_control_status_flat()` is ownership-independent. Normal
firmware entry-point fences remain intact, including on preserved ports.

The MC path reads the live core `DEVICE_TYPE` instead of assuming the FW role
cache is current: an owned port can skip normal FW role programming. It masks
the upper Link Status halfword out of Link Control writes (avoid W1C clearing)
and verifies Link Disable readback without waiting for the LTSSM transition.
`held_by_mc` is RAM bookkeeping and lasts until FW reboot; ownership release
does not clear it. This is not full OEM-command support: traffic-idle checks,
concurrent-transition exclusion and PLDM handling are explicitly deferred.

Sources: `fw/ip/se/drivers/snps_dm_ctl/dwc_dm_link_control.c:32` (live role),
`:51` (MC write policy and readback), `:109` (read-only snapshot);
`fw/ip/se/drivers/scale_engine/el_se_orch.c:1245` (public flat-port facade);
`fw/ip/se/include/el_se.h:301` (API contract).
Mocked-register tests are wired to `octl ws unit-test ip/se`, for ownership
enabled and disabled. The consuming management host/ASIC build passed.

## Auto-flush / failed re-training investigation (2026-10-26, PH-009)

Akshay Pradip Bharad reports the peer EP stays Disabled after a link-control
cycle; a devmem experiment restores operation. Exact station/build, register
writes, and silicon RTL revision were not supplied. No independent HW repro.

Verified SNPS facts (v6.42a-lca01, July 2025):
- Reference PDF pp.3306-3307, LINK_FLUSH_CONTROL_OFF: AUTO_FLUSH_EN is
  sticky, reset=1. Hot/warm/link-down reset requests initiate flushing. Turning
  it off transfers controller (and AXI bridge, if present) reset responsibility
  to the application; it is not an equivalent safe recovery implementation.
  AUTO_FLUSH_TIMEOUT reset=0xff is a pending-outbound watchdog, not proof that
  an application ACK will be synthesized.
- Reference PDF p.1993: rstctl_core_flush_req is active-high after LTSSM is
  directed to Disabled; app_flush_ack is active-high and certifies new requests
  are stalled and pending pipeline transfers completed. These signals exist for
  !AXI_RADM_SEG_BUF_ENABLE && FLUSH_CNTRL_ENABLE. ACK means assert/set, not
  merely clear the CSR. Its eventual deassert/reset/re-arm sequencing must be
  checked against the instantiated reset FSM; do not leave a stale ACK high.
- Databook section 24.5 p.704 explicitly describes request -> application drain
  -> ACK for native DPC. Section 2.4.3 pp.111-113 describes link-down/hot-reset
  flush/reset and independent app_ltssm_enable gating. Do not conflate AXI
  segmented-buffer automatic flush with native application handshaking.
- Reference PDF p.2176: Link Disable readback is immediate, not LTSSM completion.

Integration evidence:
- Generated prototype dm_ctrl_regs.h: APP_CTRL +0x20 bit5 APP_FLUSH_ACK;
  DBG_SIG +0x30 bit10 RSTCTL_CORE_FLUSH_REQ; APP_LTSSM_EN +0x18 bit0;
  LINK_DN_CTRL +0x2c bits0/1 delay reset/training. DSP/USP DBI flush control
  is +0x8cc bit0. These are relative offsets, not absolute per-port addresses.
- Published ip.se.rtl iridium at 9cb3d7d548760ce84215da2561dbd61620a9e00f:
  dm_macro/rtl/dm_pcie_iip_device_wrap.v:1483-1485 connects ACK CSR to the
  input and mirrors request into DBG_SIG. dm_wrap.v:1179-1180 connects both
  native-controller signals.
- Local /home/yoniizu/projects/iridium/rtl/ip/se at main 0b9a7bd differs:
  dm_pcie_iip_device_wrap.v:1764 ACK is open; dm_wrap.v:1383 request tied 0
  into application wrapper. It cannot validate the tested silicon wiring.
  Never infer the board's exact RTL from this checkout alone.
- Local dm_hot_rst_handler.v:56,114-118 shows configured delay gates can clear
  app_ltssm_enable after link/reset events. A correct Link Disable clear or ACK
  alone need not restart training if that independent gate remains low.
- Original FW commit 89cbb07 dwc_dm_link_control.c:51-106 only toggles/verifies
  Link Disable; no flush service. el_c2c.c:1345-1355 excludes owned ports from
  IRQ unmasking; el_se.c:718-745 only enables normal IRQs after a non-owned
  link trains. Thus normal IRQ service cannot be assumed in the MC path.

Assessment: documented integration-handshake gap is a strong explanation, not
proof of a Synopsys erratum. Both controllers can require their own local
flush/reset handling; only RP writes Link Disable. Owning EP does not logically
require disabling auto-flush: an authorized service must handle it without
violating ownership. Prefer keeping flush enabled and a non-blocking service
usable from polling/IRQ as appropriate. ACK only after actual local drain.
Validate both ends, repeated cycles, req/ack re-arming, reset/LTSSM gates,
errors/timeouts, and ownership combinations. Existing mocked-bit tests did not
simulate the partner, automatic flush, or reset state machine.
