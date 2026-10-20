# Same-index 256-GiB BAR: propagation and selector constraints

- **Recorded:** 2026-10-19
- **Status:** Verified from FW and Iridium RTL; bit-level model checked, no hardware validation
- **Tags:** same_index, bar-size-gb, 256gb, 2048gb, selectors, npa, bmc, pldm

## Findings

In the A1 0.5.152 review checkout, `bar-size-gb` becomes
`c2c_topology_preset.bar_size_gb` through `TOPO_PRESET`. SE selects the topology
before port initialization. `el_se_port_bar_size_gb()` uses that value only for
switch-attached `EL_SE_DST_SU` ports; intra-tray and B2B SU ports use the separate
`EL_SE_TRAY_SPAN_GB` policy. Both external-RP aperture programming and FW-owned
BAR programming use the helper, including later dynamic-LUT BAR rebasing.
DM converts the value to bytes with `<< 30` and writes `size-1` through CS2.
Thus the property uses GiB in practice and 256 changes the mask to
`0x0000003f_ffffffff` from the 2048-GiB `0x000001ff_ffffffff`.

Changing the size does not alter selectors or table indexing. `same_index`
uses nLUT address bits 41-46 and identity chip-bit selectors 38-40 on TX;
RX uses those selectors on switch ports and disables them on P2P ports.
Iridium RTL computes:

```
TX NPA[40:38] = table[2:0] | selected_DPA_chip_bits
RX DPA[40:38] = NPA_TO_DPA_CFG[2:0] | selected_NPA_chip_bits
```

For the complete RX address reconstruction, `c2c_axi_slave_pipe_npa_2_dpa_map.v`
assigns DPA[63:41] from `NPA_TO_DPA_CFG[25:3]`, DPA[40:38] from the selected
incoming NPA chip bits ORed with `NPA_TO_DPA_CFG[2:0]`, and DPA[37:0] directly
from the incoming address. Thus incoming NPA[63:41] (including [63:48]) is not
copied into the local DPA. FW derives the 26-bit config value from
`bar_base_addr >> 38`, preserving its [63:48] in register bits [25:10], then
ORs in device/tray indexes. Aqua's NPA table values need not mirror the local
DPA's [63:48]; those NPA bits must instead be correct for outbound fabric
addressing. This config behavior is distinct from whether `bar_base_addr` itself
is the correct local DPA base.

With same-index chip i and a 256-GiB BAR B, unchanged TX/RX selectors are
generally valid only when `B[40:38] == i`. Smaller BAR alignment alone does
not guarantee that constraint. Zero chip index can conceal failures.
A pure bit-level check over i=0..7 and BAR[40:38]=0..7 admits only 8/64 pairs.

For unconstrained fabric BAR allocation, narrow inter-sled mapping should
use the exact peer BAR plus offset on TX and reconstruct the local chip
independently of fabric BAR chip bits on RX (disable SU chip folding).
Do not blindly zero the shared topology selector property: FW currently applies
TX selectors to every C2C port, including broad-window intra-tray paths.
Keep tray-only nLUT indexing and one same-index peer per tray; a 256-GiB BAR
does not mean selecting the `all_to_all` topology.

The A1 Manageability v0.6 spec section 6.1.2.3 explicitly limits inter-sled
traffic to the same ModuleId. OEM data load supplies per-die 64-bit peer BAR
bases and 16-bit BDFs, not BAR sizes. FW copies the payload into SE shadows,
commits, and signals MW readiness; SE programs nLUT as `bar >> 38`.
External fabric enumeration/table generation must agree with the new size.
The currently selected DTS fallback and standalone sample remain separate
address-map fixtures; changing the size does not regenerate either.

The local DM-controller BAR is not necessarily left at its initial DT base.
FW initially configures a size-aligned DT base for locally configured ports.
After programming the LUT, `el_se_apply_lut_bar_bases()` rebases firmware-owned
switch-attached ports to the local chip/tray's LUT BAR slot before LTSSM enable.
An EP enumerated by an external RP is skipped so the external RP assigns its
BAR; a P2P port normally keeps the DT base unless its selected P2P base differs.

## Sources

- FW checkout: `/home/yoniizu/projects/iridium/a1_power_on_0p5p152/fw`.
- `ip/se/drivers/scale_engine/el_se_lut.c`: `TOPO_PRESET` (81),
  `el_se_lut_topology_get_table_index` (177),
  `se_lut_program_addr_sel_all_ports` (193), table programming (295).
- `ip/se/drivers/scale_engine/el_se.c`: aperture policy (249),
  aperture-only (383), initial BAR programming (405), topology selection (1284),
  local C2C init (1389), LUT-slot BAR rebasing (1865, called at 2052).
- `ip/se/drivers/snps_dm_ctl/dwc_dm_ctl.c`: mask construction (540),
  shadow mask writes (599).
- `ip/se/drivers/c2c/el_c2c.c`: local NPA-to-DPA base (109),
  selector builders (178), selector writes (229).
- RTL `Elmnt-Internal/ip.se.rtl`, `iridium` commit
  `9cb3d7d548760ce84215da2561dbd61620a9e00f`:
  `c2c_macro/rtl/c2c_axi_master_pipe_dpa_2_npa_map.v` (TX assignments);
  `c2c_macro/rtl/c2c_axi_slave_pipe_npa_2_dpa_map.v` (RX assignments).
- FW `ip/se/drivers/c2c/el_c2c.c`: `c2c_program_npa_to_dpa_cfg_impl()`
  (BAR-base extraction and CFG programming); `ip/se/drivers/scale_engine/el_se.c`:
  `el_se_get_chip_id()` (device/tray identity supplied at init).
- `ip/mng/services/el_oob/src/el_oob_oem_dataload.c`: dispatch (51, 282).
- `ip/mng/subsystems/soc/soc_providers.c`: payload staging (1104),
  commit/readiness (1155).
- `A1 Manageability Specification.docx`, effective final-markup XML:
  section 6.1.2.3, paragraphs 3652-3686 (including table paragraphs);
  same-index requirement 3665; BAR/BDF entry definitions 3673-3674.

## Validation caveat

No firmware edit/build or hardware test was performed. Check effective BAR
start/limit (`show se bar`), nonzero rail indices, arbitrary BAR allocation,
both read and write/completion paths, and intra-tray regressions. Live or
system-manager-owned ports skip normal reconfiguration.
