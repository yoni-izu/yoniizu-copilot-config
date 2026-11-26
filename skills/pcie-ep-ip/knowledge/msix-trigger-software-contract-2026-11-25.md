# MSI-X trigger API: verified software constraints

Verified 2026-11-25 against the software workspace at
`/home/yoniizu/projects/iridium/three/fw`. This note contains software facts
and explicitly attributed, source-free Neo findings, not HDL excerpts or an
assertion of hardware delivery.

## Addressing correction (implemented)

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

The user explicitly approved extending the original EP-only scope to the
PCIe binding/configuration and A1/A2 project DTS mapping. Both host PIW nodes
now expose the gateway instance as the optional `msix_gw_regs` named range;
the PIW config initializer derives its address and `has_msix_gw` capability
from DT. Older configurations without this range return `-ENOTSUP`.
There is no hardcoded displacement or dependency on another device's mailbox.

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
  void-returning helper. The trigger API promises that a write was
  issued, not bus-error detection or interrupt delivery.

## Neo hardware evidence and limitations

Source-free Neo follow-ups on 2026-11-25 confirmed:

- The gateway uses only vector bits 10:0, not the controller doorbell's
  PF/VF payload fields. VF-active comes from control AWUSER metadata bit 7.
- The configured PF count is one, so the gateway selects PF0. An aligned
  32-bit write at gateway offset zero supplies the low vector bits.
- The gateway's MSI-X enable, vector-index mask, security policy and
  coalescing remain platform prerequisites, not trigger-API setup.

Evidence: `ip.pcie.rtl`, `piw_macro/rtl/msix_gw_handler.v`
(`msix_gw_handler`, A1 `iridium` 3afb411, lines 241, 291-301, 394;
A2 98f0e9f0, lines 244, 294-304, 399);
`piw_macro/rtl/piw_pkg.v` (`msix_ctrl_wdata_t`, A1 lines 142-144,
A2 lines 145-147); `piw_macro/rtl/piw_lbw_ctrl.v`
(A1 lines 2287-2293); `global_defines.v` (`EL_N_PF`, A1 line 57,
A2 line 73). The first handler query used A1 mirror HEAD d5be1ef7;
the follow-up explicitly checked A1's frozen `iridium` ref instead.

Neo traced the CM7 source metadata as zero by default, unless per-region
attribute overrides change it (`ip.armcpu.rtl`, `cpu_addr_extnd.v`,
A1 `iridium` 588c20e lines 282-289, A2 de913df lines 295-299).
It did **not** complete the ELBI, PTuC/MuC or intervening FIU-override trace.
Do not treat those upstream paths as independently verified. The API requires
normal VF-inactive firmware transaction metadata and does not change it.
A2 mirror evidence was from the 2026-11-22 snapshot, without refresh.

## Implementation status

Implemented in the approved workspace, uncommitted:

- Public syscall/dispatch contract: `ip/pcie/ep/include/el_pcie_ep.h`.
- Endpoint-to-PIW production dispatch:
  `ip/pcie/ep/drivers/el_pcie_ep/el_pcie_ep_msix.c`.
- Ownership-gated PIW operation: `ip/pcie/ep/include/el_piw_api.h`;
  single-write implementation: `ip/pcie/ep/drivers/el_piw/el_piw_msix.c`.
- Optional binding: `ip/pcie/ep/dts/bindings/pcie_ep/el,pcie-ep-piw.yaml`.
- Project mappings: `proj_infra/soc/dts/{a1/a1_hsi,a2/a2_hsi}.dtsi`.
- Focused behavioral coverage: `ip/pcie/ep/drivers/el_pcie_ep/tests/test.c`,
  compiling the actual operation sources with test-only host/I/O seams.
- Contract and CI requirements: `ip/pcie/ep/docs/msix-trigger.md`.

The API enforces existing ownership, rejects unencodable vectors rather than
truncating, targets only the host PIW, and issues one write without reading,
polling, waiting, configuring MSI-X or changing masks/security/coalescing.
The general SV helpers remain unchanged.

Static architecture checks found no new errors/warnings (92 existing
conditional-compilation findings in edited legacy headers/sources remain
unchanged). Worktree checkpatch, clang-format, whitespace and Zephyr binding
validation passed. The read-only firmware review's sole CMake source-list
formatting finding was corrected. No firmware builds, firmware tests, VM
builds, or hardware delivery checks were run; CI must compile and exercise
the supported CSR selections before runtime verification is claimed.
