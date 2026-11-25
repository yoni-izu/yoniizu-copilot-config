# MSI-X trigger API: verified software constraints

Verified 2026-11-25 against the software workspace at
`/home/yoniizu/projects/iridium/three/fw`. This note contains software facts
only, not HDL excerpts or an assertion of hardware delivery.

## Addressing blocker

Do not promote the existing SV helper's address expression into production.

- `infra/include/el_glob_addr.h:54` obtains `EL_CONTROL_BASE` from the global
  address-space devicetree node's `control` register range. It is not a PCIe
  MSI-X gateway instance address.
- `proj_infra/soc/dts/a1/a1_glob_params_default.dtsi:47-57` defines that global
  control range separately from the PCIe instance map.
- `proj_infra/soc/dts/a1/a1.dtsi:54-56` builds the existing MSI-X notification
  address from `D0_CTRL_PCIE_EP_CLS_PIW_CTRL_MSIX_GW_0_LO` plus
  `MSI_GW_DOORBELL_ADDR`, with the corresponding instance high address.
- `proj_infra/soc/dts/a2/a2.dtsi:130-134` uses the same instance-address pattern.
- The generated software header
  `build_artifacts/include/projects/a1/3_0_11_3p0_1389/bases/fw/native/project_bases_fw.h:76451-76454`
  gives this gateway instance address as `0x0100000008403000`, whereas the
  global control range starts at `0x0100000008000000`. Offset zero is not a
  substitute for the missing gateway instance base.
- `ip/pcie/ep/drivers/el_piw/el_piw_sv.c:855-874` instead uses the global
  control base plus the register offset. Existing SV code is therefore not
  sufficient addressing evidence for a new production operation.

The existing PIW device does not expose the MSI-X control-fabric gateway as a
named register range: see `proj_infra/soc/dts/a1/a1_hsi.dtsi:2659-2681`,
`proj_infra/soc/dts/a2/a2_hsi.dtsi:53-89`, and the config initializer in
`ip/pcie/ep/drivers/el_piw/el_piw.c:2548-2561`.
Correct project-agnostic production wiring needs a DT-provided gateway mapping,
not a hardcoded displacement or a dependency on another device's mailbox.

## Encoding and success contract

- The generated software CSR header
  `build_artifacts/include/ips/pcie/v1/3_0_11_3p0_1389/prototype/msi_gw_regs.h:22-27`
  defines a writable register at offset zero with a 20-bit value field. That
  field alone does not establish MSI-X vector or function semantics.
- `build_artifacts/include/prototype/dwc_pcie_usp_regs.h:6000-6032` describes
  the controller doorbell's 11-bit vector encoding. An encoding limit is not
  the host-programmed table size or the gateway's acceptance policy.
- `proj_infra/scripts/gen_ral.sh` successfully generated the ignored RAL
  headers for `a1/3_0_11_3p0_1389`, `a2/1_3_0_1p3_870`, and
  `a2/1_4_0_1p4_1047`. Each exposes the generated gateway offset and DWC
  doorbell field aliases. No generated source was hand-edited.
- `ip/pcie/ep/include/el_pcie.h:29-41` makes register writes through a
  void-returning helper. A future trigger API can promise that a write was
  issued, not bus-error detection or interrupt delivery.

## Implementation status

The proposed vector-only host-EP/PF0 production API was not implemented during
this verification. The approved plan's SV-derived addressing premise failed
verification before firmware edits. The requested EP-worktree-only scope does
not include the project DTS mapping needed for a correct implementation.

Revalidate routing and transaction metadata through Neo before implementation;
do not infer effective PF/VF selection from the controller's payload fields
alone. No firmware builds, firmware tests, or hardware delivery checks were
run.
