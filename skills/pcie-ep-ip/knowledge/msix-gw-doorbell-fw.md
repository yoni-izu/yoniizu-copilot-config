# MSI-X Gateway doorbell — FW trigger contract

**Status:** verified from generated SW CSR headers + FW sources (no RTL read). Recorded 2026-11-25.

## Findings

- Gateway register `MSI_GW_DOORBELL_ADDR` is at offset **0x0** of the gateway
  instance; single field `VAL`, **20 bits** (`VAL_MSK 0xfffff`), write-only
  (`SW_ACCESS 0x2`). Same in v1 (A1) and v2 (A2) layouts.
  Source: `build_artifacts/include/{v1,v2/...}/prototype/msi_gw_regs.h`.
- The payload mirrors the DWC controller `MSIX_DOORBELL_OFF` layout: vector
  (`MSIX_DOORBELL_VECTOR`, 11 bits → 0..2047) plus TC / VF_ACTIVE / VF / PF
  fields. PF0 with VF inactive → all non-vector bits zero, so payload == vector.
  Source: `ip/pcie/ep/drivers/el_piw/el_piw_sv.c` `el_piw_sv_msix_doorbell_write`.
- Gateway instance address (die 0, host PIW):
  `D0_CTRL_PCIE_EP_CLS_PIW_CTRL_MSIX_GW_0_{HI,LO}` in project bases
  (A2: 0x1_0000_0000 | 0xA0403000). The SV helper writes
  `EL_CONTROL_BASE + MSI_GW_DOORBELL_ADDR`, i.e. the global control base — NOT
  the gateway aperture; do not copy it for production.
- Production API (added 2026-11-25, uncommitted at record time):
  `el_pcie_ep_msix_trigger(uint32_t vector)` → host EP → PIW child →
  `el_piw_msix_trigger()`; gateway address from optional PIW DT reg-name
  `msix_gw_regs` (mapped in `proj_infra/soc/dts/{a1/a1_hsi,a2/a2_hsi}.dtsi`).
  Doc: `ip/pcie/ep/docs/msix-trigger.md`.

## Open

- Gateway validation/coalescing behaviour (enable check, max index, spoofing
  checks on PF/VF vs AXI metadata) not re-verified here; ask Neo if needed.
