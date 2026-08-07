# C2C Board Topology → Scale Engine LUT Driver Configuration

- **Recorded:** 2026-07-09
- **Status:** Verified from FW driver + DeviceTree
- **Tags:** topology, c2c, se, scale engine, lut, nlut, npa, cam, rail, rail_device_list, nlut_select_bits, selector_cfg_addr_bit, num_rails, bar_size_gb, all_to_all, pairs, halves, quads, address translation, preset, driver configuration

## Scope / Question
How do the different C2C board topologies affect the Scale Engine (EL SE) / C2C
driver configuration? What does selecting a topology actually change in HW programming?

## Findings

A "topology" is a `struct c2c_topology_preset` selected at bring-up. Each is a DT
node (`compatible = "el,c2c-topology"`) compiled into a static preset table. ALL C2C
LUT programming is derived from that one preset — the C2C driver stays topology-agnostic
(pure per-port register writer); topology policy lives in the SE LUT orchestration.

### 6 presets (enum `c2c_board_topology`)
all_to_all(0), pairs_even_odd(1), pairs_0145_2367(2), halves_0123_4567(3, DEFAULT),
quads_adjacent(4), quads_stride(5).

### The 5 preset fields and what each drives
| Field | HW effect |
|-------|-----------|
| `nlut_select_bits[6]` | The 6 DPA address bits used to index the 64-entry nLUT → programmed into `DPA_TO_NPA_ADDR_SEL`. Different topology → different bits → same DPA routes to different peer. |
| `selector_cfg_addr_bit[3]` | Rail-selector address bits in the addr-sel register. More rails → more selector bits used. |
| `num_rails` | Derives `devices_per_rail = EL_GLOB_NUM_DEVICES_PER_TRAY / num_rails`; feeds table-index math + validation. |
| `rail_device_list[8]` | Maps physical `chip_idx` → rail + position → NPA/CAM table slot. Defines connectivity grouping (who talks to whom). |
| `bar_size_gb` | C2C BAR aperture (256/512/1024 GB). |

### Concrete per-topology values (a1_hsi.dtsi)
| Topology | Rails | BAR GB | nlut_select_bits | selector_cfg_addr_bit | rail_device_list grouping |
|----------|-------|--------|------------------|-----------------------|---------------------------|
| all_to_all       | 1 | 256  | 38 39 40 41 42 43 | 0 0 0   | {0..7} |
| pairs_even_odd   | 2 | 512  | 39 40 41 42 43 44 | 38 0 0  | {0,2,4,6}{1,3,5,7} |
| pairs_0145_2367  | 2 | 512  | 38 40 41 42 43 44 | 39 0 0  | {0,1,4,5}{2,3,6,7} |
| halves_0123_4567 | 2 | 512  | 38 39 41 42 43 44 | 40 0 0  | {0,1,2,3}{4,5,6,7} |
| quads_adjacent   | 4 | 1024 | 38 41 42 43 44 45 | 39 40 0 | {0,1}{2,3}{4,5}{6,7} |
| quads_stride     | 4 | 1024 | 40 41 42 43 44 45 | 38 39 0 | {0,4}{1,5}{2,6}{3,7} |

Pattern: as num_rails 1→2→4, more low address bits become rail selector bits, the
nlut_select_bits window shifts up, and bar_size_gb doubles each step.

> **7th topology — `same_index` (8 rails, 2048 GB):** extends this pattern one more step
> (fold all 3 device bits `<38 39 40>`, nlut_select_bits `<41 42 43 44 45 46>`, identity
> rail_device_list `<0..7>`). Chip-architect decision (Maxim Leshin); binding + DTS landed
> under IPSESW-295, driver preset/enum wiring still pending. See
> `c2c-same-index-topology.md`.

### Driver consumption flow (all in el_se_lut.c)
1. `se_lut_select_topology()` — stores active preset, resolves `self_rail` via
   `se_lut_find_rail()` (returns `i / devices_per_rail` from rail_device_list).
2. `se_lut_validate_topology_params()` — rejects `num_compute_trays * devices_per_rail
   > C2C_REQ_ID_MAX+1` (64). Topology bounds max supported trays.
3. `se_lut_program_addr_sel_all_ports()` → `el_c2c_lut_write_addr_sel()` writes
   nlut_select_bits + selector_cfg_addr_bit to every port/die.
4. `el_se_lut_topology_get_table_index()` = `tray_idx*devices_per_rail + (i%devices_per_rail)`
   using rail_device_list → NPA/CAM shadow slot per peer.
5. `se_lut_write_tables_to_hw()` pushes NPA/CAM shadows; `el_se_lut_program_remote_req_id()`
   resolves each peer's CAM entry via the active preset.

### Address bit layout (context)
DPA→NPA: nlut_index = extract_bits(dpa, nlut_select_bits[0..5]) (6 bits); nLUT entry =
NPA[63:38] (26 bits); npa = {npa_high, dpa[37:0]}. NPA top bits encode device[40:38],
tray[43:41], rack[45:44]. `C2C_ADDR_BIT_BASE=36`, `C2C_SELECTOR_CHIP_IDX_ADDR_BIT_BASE=38`.

## Sources
- FW: `ip.se.sw.fw` → `fw/ip/se/drivers/scale_engine/el_se_lut.c`
  (`topology_presets`, `TOPO_PRESET`, `se_lut_select_topology`, `se_lut_find_rail`,
  `se_lut_validate_topology_params`, `se_lut_program_addr_sel_all_ports`,
  `el_se_lut_topology_get_table_index`, `se_lut_write_tables_to_hw`).
- FW: `fw/ip/se/include/el_se_lut.h` (`enum c2c_board_topology`, `struct c2c_topology_preset`).
- FW: `fw/ip/se/drivers/c2c/el_c2c.c` (`el_c2c_lut_write_addr_sel_impl`, NPA/CAM builders).
- DT: `fw/proj_infra/soc/dts/a1_hsi.dtsi` (c2c_topo_* nodes, per-topology values; default
  = halves_0123_4567 via `topology-ref`).
- Ref: `fw/ip/se/.github/skills/ipse-se-expert/references/address-translation.md`.
- Related KB: `scale-up-tray-rack-topology.md` (physical tray/rack rail topology).
