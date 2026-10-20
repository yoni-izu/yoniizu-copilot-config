# C2C `same_index` Topology (8-rail) — Design + Architect Decision

- **Recorded:** 2026-07-22
- **Status:** Verified from FW driver + DeviceTree + chip-architect directive (Maxim Leshin); calculation architect-approved, emulation verification pending
- **Tags:** topology, same_index, c2c, se, scale engine, 8 rail, 8-rail, 2048gb, bar_size_gb, nlut_select_bits, selector_cfg_addr_bit, rail_device_list, same index, chip index, sled, tray, rack, address translation, ipsesw-295, maxim leshin, architect decision

## Scope / Question
What is the C2C `same_index` topology, why does it exist, and how are its DT preset
values derived? Captures the chip-architect decision behind it and the current
implementation state.

## Design intent (architect directive)
Per chip architect **Maxim Leshin**, the product only needs **same-index chip
connectivity**: chip *i* on a tray/sled connects **only** to chip *i* on every peer
tray — no cross-index links (chip[X] ↔ chip[Y], X≠Y) and no in-tray peers. General
rail connectivity is deliberately *not* supported, because supporting it would force
per-customer reconfiguration. This makes each device alone on its own rail → an
**8-rail** system (one rail per chip index, 8 chips per tray).

FW is coupled to the existing rail-based topology preset structure, so rather than
writing new routing logic, `same_index` is expressed as **another `c2c_topology_preset`**
that reuses the same driver mechanism (see `topology-driver-configuration.md`).

## Preset values (7th topology)
Node `c2c_topo_same_index` (`compatible = "el,c2c-topology"`):

| Field | Value | Meaning |
|-------|-------|---------|
| `topology-type` | `"same_index"` | new string value |
| `num-rails` | `8` | one rail per chip index; `devices_per_rail = 8/8 = 1` |
| `bar-size-gb` | `2048` | 256 GB × 2³ (all 3 device bits folded) |
| `nlut-select-bits` | `<41 42 43 44 45 46>` | 6-bit nLUT index from bits [46:41] |
| `selector-cfg-addr-bit` | `<38 39 40>` | folds all 3 device-index bits into the window |
| `rail-device-list` | `<0 1 2 3 4 5 6 7>` | identity — rail *j* = {device *j*} |

### Bit math (why these values)
- **Fold** = a device-index bit (38/39/40) is routed straight through into the address
  window instead of selecting a rail. Folding all 3 device bits (`selector-cfg-addr-bit
  = <38 39 40>`) makes a device always reach the *same-index* device in the peer tray,
  and grows the per-entry window to 256 GB × 2³ = **2048 GB**.
- With all device bits folded, the 6-bit nLUT index is taken from the next-higher bits
  **[46:41]** (tray + rack fields), so the DPA→NPA table is effectively indexed by
  tray/rack, not by device.
- **Bit 46 is valid**: offset `46 − C2C_ADDR_BIT_BASE(36) = 10 = C2C_NLUT_BIT_OFFSET_MAX`
  — exactly at the maximum allowed nLUT bit offset. Each topology mechanically takes the
  6 lowest non-folded address bits ≥ 38; folding 3 bits shifts that window up to 41–46.
- General pattern (extends the 6-preset table): num_rails 1→2→4→**8**, folded bits
  0→1→2→**3**, bar_size_gb 256→512→1024→**2048**, nlut window shifts up each step.

## Implementation state (IPSESW-295)
**Update 2026-10-15:** The implementation status below is historical. The A1
0.5.152 review checkout now wires `C2C_TOPO_SAME_INDEX` into both preset/name
tables and selects it from DTS before port bring-up. The old 2048-GiB calculation
is not a requirement for the revised same-index-only software routing contract:
see [256-GiB BAR propagation and selector constraints](same-index-256gb-bar-propagation.md).
Shrinking the BAR alone does not disable address-bit folding.

- **Landed (DTS/binding side, under review):**
  - `ip.se.sw.fw` PR #123 — widens `el,c2c-topology.yaml`: `num-rails` enum += 8,
    `bar-size-gb` enum += 2048, `topology-type` docs += `same_index`.
  - `project.iridium.sw.fw.infra` PR #959 — adds the `c2c_topo_same_index` node to
    `soc/dts/a1_hsi.dtsi`.
- **NOT yet wired (driver side):** `enum c2c_board_topology` (`el_se_lut.h`) has no
  `C2C_TOPO_SAME_INDEX`, and `topology_presets[]` / `topo_name_map[]` (`el_se_lut.c`)
  have no `same_index` row. Until that lands, the node is defined but **unreachable**
  (never referenced via `TOPO_PRESET(same_index)`) and the topology cannot be selected.
  DTS/binding are build-clean regardless.

## Architect conversation (author ⇄ Maxim Leshin)

> **Maxim Leshin — 8:45 PM:** I don't want to support rails connectivity at all.
> Otherwise, we will have to change it per customer configuration. From the
> application point of view, we plan only to Chip with the same index together. No
> need to connect Chip[X] to Chip[Y] on a different sled. Your later understand is
> correct.

> **Author (Akshay) — 8:47 PM (edited):** ok. I would need nlut-select-bits,
> selector-cfg-addr-bit for this topology. It would be 8 rail kind of system I
> believe.

> **Author — (edited):** Firmware is coupled with the initial rail-based topology
> logic. All the configuration is driven based on this structure. I don't want to
> code a whole new logic at this time. So I need your or Sagis help in building this
> structure.
> _(referenced the existing `c2c_topo_halves_0123_4567` node as the structural template:
> num-rails=2, bar-size-gb=512, nlut-select-bits=<38 39 41 42 43 44>,
> selector-cfg-addr-bit=<40 0 0>, rail-device-list=<0 1 2 3 4 5 6 7>.)_

> **Author — 9:39 PM (edited):** Basis my understanding - it would look like this
> ```
> c2c_topo_same_index: c2c_topo_same_index {
>         compatible = "el,c2c-topology";
>         topology-type = "same_index";
>         num-rails = <8>;
>         bar-size-gb = <2048>;
>         nlut-select-bits = <41 42 43 44 45 46>;
>         selector-cfg-addr-bit = <38 39 40>;
>         /* 8 rails: rail j = {device j} (identity) */
>         rail-device-list = <0 1 2 3 4 5 6 7>;
> };
> ```

> **Maxim Leshin — 1:52 PM:** Yes, the calculation seems OK to me. It would be a good
> idea to verify that in emulation 👍

**Takeaways:** (1) `same_index` was an explicit product/architecture decision, not an
arbitrary FW choice — general rail connectivity is intentionally out of scope to avoid
per-customer config. (2) The concrete preset values (8 rails / 2048 GB / bits [46:41] /
fold [40:38]) were author-proposed and **architect-approved**, with the standing action
to **verify in emulation** before it's fully trusted.

## Sources
- DT: `project.iridium.sw.fw.infra` → `soc/dts/a1_hsi.dtsi` (`c2c_topo_same_index`).
- Binding: `ip.se.sw.fw` → `dts/bindings/el,c2c-topology.yaml`.
- FW (consumer, pending): `ip.se.sw.fw` → `drivers/scale_engine/el_se_lut.c`
  (`topology_presets`, `TOPO_PRESET`), `include/el_se_lut.h` (`enum c2c_board_topology`).
- Constants: `C2C_ADDR_BIT_BASE=36`, `C2C_NLUT_BIT_OFFSET_MAX=10` (`el_se_lut.c`),
  `C2C_SELECTOR_CHIP_IDX_ADDR_BIT_BASE=38` (`el_c2c.h`).
- Directive: chip architect **Maxim Leshin** (Teams, 2026-07-21/22) — see conversation above.
- JIRA: IPSESW-295. PRs: `ip.se.sw.fw` #123, `project.iridium.sw.fw.infra` #959.
- Related KB: `topology-driver-configuration.md` (the 6-preset mechanism this extends).
