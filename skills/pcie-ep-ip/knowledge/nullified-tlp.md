# Nullified TLP — discarded before transaction-layer processing

- **Recorded:** 2026-10-28
- **Status:** Verified from PCIe controller databook and public vendor documentation
- **Tags:** pcie, nullified tlp, data link layer, packet discard, poisoned tlp

## Finding

A nullified TLP is a packet marked by the link-side handling as nullified and therefore
discarded rather than acted on as a transaction. The receiver/controller must not deliver it
for transaction processing; any packet resources/context held for it are released. Nullification
is not the same as a poisoned TLP: a poisoned TLP is a TLP flagged with the header EP bit,
whereas a nullified TLP is discarded before it can have transaction effects.

### TX Nullified TLP counter / hardware paths

The Synopsys RAS DES Event Counter Group 6, event `0x05`, is named **TX Nullified TLP**
(controller databook p.721). A non-zero event counter records an occurrence; it is not itself a
live error-mode/status bit.

The controller databook documents these ways a TX-side nullified TLP can occur:

1. **RAS DP Error Mode after an uncorrectable RASDP error.** On the first uncorrectable RAM or
   datapath/interface-protection error, `AUTO_LINK_DOWN_EN=1` forces link-down/reset;
   `AUTO_LINK_DOWN_EN=0` enters RAS DP Error Mode (p.679). The documented uncorrectable classes
   include double-bit ECC and all parity errors (p.680 footnote 107). In error mode:
   - 32/64-bit datapath: the bad TLP, subsequent TLPs, and an in-progress replay are nullified
     and transmitted (EDB inserted and LCRC inverted for the bad TLP); subsequent replays are
     silently dropped.
   - 128/256/512-bit datapath: the bad and subsequent TLPs are silently dropped, but an
     in-progress replay is still nullified and transmitted; subsequent replays are silently
     dropped (p.680).
   The controller may also nullify earlier queued TLPs (p.680 footnote 105). Thus datapath width
   matters: the TX nullified counter does not imply every rejected TLP was put on the wire.
2. **CXS-protected datapath, if configured.** CXS data/control protection errors can nullify an
   ongoing TLP (p.468–469). This is a separate optional CXS path; its enablement in Iridium was
   not established from the locally available source.
3. **IDE Security Module indication.** The controller/IDE interface has a TX nullified-packet
   indication (`ide_pcie_tx_dllp_error` into the controller, p.485). The controller databook
   does not state the IDE module's internal causes; the IDE Security Module documentation/source
   is needed to identify them.
4. **Intentional error injection, if enabled.** Synopsys lists TLP Duplicate/Nullify Error among
   RAS DES injection types; it requires `CX_RAS_DES_EINJ_ENABLE=1` (p.711). This is a test path,
   not evidence of spontaneous link traffic.

### A1 wrapper RASDP diagnostic CSRs

The A1 `PEP_CTRL_AUX_RAS_DP` register is at auxiliary-block offset `0x98`. Bit 0 reports
`SLV_RASDP_ERR_MODE`; bit 1 reports `MSTR_RASDP_ERR_MODE`. The auxiliary
`INTERRUPT_INTR_SEI_CAUSE` register is at offset `0x11c`; bits 16–19 report RASDP uncorrectable
error, correctable error, slave error mode, and master error mode, respectively. These wrapper
CSRs distinguish current mode status from the associated cause indications; a snapshot cannot
establish historical state if software has cleared causes or the mode has ended.

For completion-timeout triage, the PEP core `INTERRUPT_INTR_SEI_CAUSE` bit 4 is
`RADM_CPL_TIMEOUT`. `RX_CPL_TO` captures timeout metadata including the completion length field,
and `RX_CPL_TO_TAG` captures the timed-out tag. In the auxiliary SEI cause register, bit 22 is
`CFG_SEND_NF_ERR`; it indicates that the non-fatal-error send cause was recorded, but does not
identify the originating error on its own.

The receive Completion LUT watches for PCIe Completions corresponding to previously transmitted
Non-Posted Requests; this timeout therefore concerns a request sent by the endpoint awaiting its
completion, not the endpoint failing to complete an inbound host request. The latter is the
separate target Completion LUT / `TX_CPL_TO` path (databook p.260). After a timeout, Synopsys
recommends resetting Completion queue management after waiting for the queue and NP-request LUT
to drain (p.536). `DBG_SIG` exposes both `RDLH_LINK_UP`, `SMLH_LINK_UP`, and the six-bit LTSSM
state; interpret these as a snapshot only, not a timeline.

### Iridium RTL visibility

The Iridium checkout's Element wrapper accepts `ras_dp_unc_err`, `ras_dp_cor_err`,
`slv_rasdp_err_mode`, and `mstr_rasdp_err_mode` from the controller and routes the RASDP error
mode into its surrounding logic (`pep_macro/rtl/pcie_iip_device_wrap.v`, lines 739–744 and
2255–2257). Its compile list includes Synopsys `rasdp_error_mode_control.sv`
(`pep_macro/misc/elmnt_u_core_top.lst`, line 886). However, the actual `soft_links/eip_pcie`
target is not present in the inspected environment, so the licensed Synopsys RTL implementing
the counter and TX nullification is not locally readable. These sources establish the integration
signals, not a complete RTL-level proof of every counter increment condition.

The Synopsys controller databook also says received link-layer TLPs marked DLLP-errored or
nullified are excluded from ordering and may be discarded by the controller or application
(p.160). A public Intel/Altera knowledge-base article describes nullified TLP handling as
discard-and-release-resources; the PCI-SIG Base Specification itself is member/purchase
documentation, so the exact normative wording and section should be checked there.

## Sources

- Synopsys, `DWC_pcie_ctl_ep_databook.pdf`, v6.42a-lca01, p.160 (link-layer nullified TLPs are
  not considered for ordering and may be discarded), pp.468–469 (CXS protection errors),
  p.485 (IDE nullified-packet indication), pp.679–680 (RASDP error-mode entry and TX behavior),
  p.260 (receive vs target Completion LUT timeout distinction), p.536 (timeout queue-management
  recovery recommendation), p.711 (error injection), and p.721 (RAS DES event counter).
- Iridium A1 generated CSR headers: `pep_ctrl_aux_regs.h:586-594,784-846` and
  `pep_ctrl_aux_regs_attr.h:303-307,398-420` under
  `fw/build_artifacts/include/v1/prototype/`.
- Iridium A1 generated CSR headers: `pep_ctrl_regs.h:347-376,519-536` and
  `pep_ctrl_regs.h:260-282,591-610`, `pep_ctrl_aux_regs.h:1017-1065` under
  `fw/build_artifacts/include/v1/prototype/`.
- Iridium RTL: `ip/pcie/pep_macro/rtl/pcie_iip_device_wrap.v:739-744,2255-2257`;
  `ip/pcie/pep_macro/misc/elmnt_u_core_top.lst:886` (Synopsys RTL source list; linked source
  unavailable in the inspected environment).
- Intel/Altera, [Why does the Intel Arria 10 FPGA PCIe Hard IP treat nullified TLPs...](https://community.altera.com/kb/knowledge-base/why-does-the-intel%C2%AE-arria%C2%AE-10-fpga-pcie-hard-ip-treat-nullified-tlps-including-p/346431)
- PCI-SIG, [PCI Express Base Specification overview](https://pcisig.com/specification-overview/pci-express-base)
