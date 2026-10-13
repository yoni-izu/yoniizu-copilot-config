# PHY-FW SRAM Copy FSM — two triggers (PRST + SW), separate config regs; main vs iridium

- **Recorded:** 2026-09-13
- **Status:** Verified from RTL (both branches)
- **Tags:** fw_sram_copy_handler, phy fw copy, sram copy fsm, sram_copy_fsm_t,
  prst trigger, sw trigger, prst_pending, csr_cfg_sram_sw_copy_start,
  prst_from_rom, ccm cfg fast access, ppw_prst_sram_copy, ppw_sw_sram_copy,
  two triggers, separate config, broadcast chicken, prst abort, main vs iridium,
  atlantic vs iridium, iccm dccm depth, vrfid, num_of_phy, iccm, dccm

## Scope / Question
Confirm the HW **PHY-FW COPY FSM** has **two triggers — PRST and a SW trigger —
each with its own separate configuration registers**, fully explore the FSM
logic in RTL, and distinguish the `main` (Atlantic) vs `iridium` (A1 freezed-rtl)
branches. Module = `fw_sram_copy_handler` (copies PHY FW ICCM/DCCM from the PIW
FW SRAM — or the PHY internal ROM — into the PHY CCM memories over the MNG AXI).

## Findings

### Statement CONFIRMED — two independent triggers, separate config regs
The single FSM `sram_copy_fsm_t` (reg `sram_copy_fsm_ps`) is entered from
`SRAM_COPY_FSM_IDLE` by exactly two mutually-exclusive triggers, **PRST first**:

```
IDLE -> prst_pending_start        ? PRST_CCM_CFG_RD                       // PRST trigger (priority)
      : csr_cfg_sram_sw_copy_start? (ccm_type ? SW_ICCM : SW_DCCM)        // SW trigger
      : IDLE
```
This IDLE-arbitration line is **byte-identical on `main` and `iridium`** — the
two-trigger architecture is unchanged A1→A2.

**Trigger 1 — PRST (HW-autonomous, edge-driven):**
- `prst_de_assert = perst_n==1 & perst_n_r==0` (PERST **rising**/de-assert edge)
  latches `prst_pending`.
- `async_psoc2pep_perst_n` is a **watched level input** to the FSM (synced via
  `el_cdc_sync` + sampled), NOT a reset. The FSM's own reset is the separate PIW `rst_n`
  (`piw_main.v` wires `.rst_n(rst_n)`, `.async_psoc2pep_perst_n(async_psoc2pep_perst_n)`).
  So *mechanically* the FSM only sees the PERST waveform and does not know its source.
- **BUT whether it autonomously fires depends on whether the PIW reset domain survives the
  PERST event** — and per Iridium reset-domain Table 4-2 (`iridium-prj/knowledge/reset-domains.md`)
  **Warm reset resets BOTH PEP *and* PIW** (PIW also on PoR and WD). So:
  - **GPIO-pad-sourced PERST** (host fundamental reset, `prst_select_source=1`): the pin
    toggles but the PIW unit reset is **not** asserted → the copy FSM stays alive → it
    **autonomously re-copies PHY FW on the PERST de-assert edge.** This is the intended HW path.
  - **Reset-manager-sourced PERST** (`prst_select_source=0` → `async_unit_rst_out_n[16]`,
    the ROM/warm path, see `psoc-ip/knowledge/perst-source-select.md`): a Warm/PoR flow that
    drives this PERST **also resets PIW**, so the copy FSM is **wiped** (pending + `{i,d}ccm_init_done`
    cleared) and does **NOT** self-fire via `prst_pending`. Re-copy is then done by the
    surviving actor: **PoR → Boot ROM** (`prst_from_rom` path); **Warm → the MuC FW**, which
    is *not* reset by Warm (Table 4-2) and re-runs PIW init + re-copies PHY FW via the
    **SW-triggered** copy (`sram_sw_copy_start`) in its warm-reset POST callback.
- Net: the PRST *edge*-triggered HW copy is really the **GPIO/host-PERST** path. The
  reset-manager (warm/PoR) path re-copies via FW/ROM, not via this autonomous FSM trigger.
- Fires only when gated:
  `prst_pending_start = ((iccm_sram_copy_init_done & dccm_sram_copy_init_done)
  | prst_from_rom) & (fsm==IDLE) & prst_pending & psoc2pep_perst_n`.
  So a PERST-driven copy needs **both `{I,D}CCM_SRAM_COPY_INIT.DONE`** set
  (SRAM pre-loaded), *unless* `prst_from_rom` bypasses that (copy from ROM).
- **One PRST trigger copies BOTH CCMs**: DCCM then ICCM, preceded by a PHY
  "CCM fast-access select" read-modify-write; status `csr_cfg_prst_copy_done[3:0]`
  (0-DCCM 1-ICCM 2-DCCM-phy1 3-ICCM-phy1).
- Abort: `sram_copy_prst_abort = ~perst_n & ~csr_cfg_prst_assert_cont` — a PERST
  re-assert mid-copy jumps to `PRST_ICCM_DONE` unless `prst_assert_cont` says
  keep going.
- **PRST config regs (separate):** `csr_cfg_prst_from_rom`,
  `csr_cfg_prst_assert_cont`, `csr_cfg_iccm/dccm_sram_copy_init_done`,
  `csr_cfg_ppw_ccm_fast_access_addr`/`_write_data` (used only in the CCM-CFG
  RD/WR steps), `csr_cfg_broadcast_chicken` (re-run for phy1). CSR block =
  `PPW_PRST_SRAM_COPY_CFG` (0xc) + `PPW_PRST_SRAM_COPY_DONE` (0x28).

**Trigger 2 — SW (FW-driven, direct):**
- `csr_cfg_sram_sw_copy_start` (single-shot). `ccm_type` picks the target:
  `SW_ICCM` (1) or `SW_DCCM` (0) — **one CCM per trigger** (FW triggers twice
  to load both), no CCM-CFG fast-access step, no broadcast.
- Path: `IDLE -> SW_{D,I}CCM -> SW_DONE -> IDLE`; completion pulse
  `csr_sts_sram_sw_copy_done` = (`fsm==SW_DONE` & `ns==IDLE`).
- **SW config regs (separate):** `csr_cfg_sram_sw_copy_size`, `_offset`,
  `_ccm_type`, `_phy_idx`, `_rom` (BOOT_FROM_ROM: reads PHY ROM mems instead of
  PIW FW SRAM). CSR block = `PPW_SW_SRAM_COPY_START/DONE/CFG` (0x0/0x4/0x8).
- **Shared by both paths:** dest base `csr_cfg_copy_base_addr_{iccm,dccm}`
  (CSR 0x10/0x18, `{phy1,phy0}`) and the AXI-gen rd/wr tunables.

### Full FSM state list (`sram_copy_fsm_t`, both branches)
`IDLE(0)` → PRST arm: `PRST_CCM_CFG_RD(1)` → `..._RD_CPL(2)` → `..._CFG_WR(3)` →
`..._WR_CPL(4)` (read/modify/write the PHY CCM fast-access-select via MNG AXI) →
`PRST_DCCM(5)` → `PRST_DCCM_DONE(9)` (drain outstanding) → `PRST_ICCM(6)` →
`PRST_ICCM_DONE(10)` → (broadcast-chicken & phy_idx==0 ? back to `CFG_RD` for
phy1 : `IDLE`). SW arm: `SW_DCCM(7)` **or** `SW_ICCM(8)` → `SW_DONE(11)` →
`IDLE`. AXI gen rd/wr errors with `rst_fsm_at_gen_err` bounce to `IDLE`.
Datapath source select: `sram_copy_from_rom` (PRST `prst_from_rom` or SW `_rom`)
reads `sram_fw_mem_{i,d}ccm_rom` mems; else the PIW FW SRAM via a round-robin
arbiter shared with the MNG-upstream SRAM-write port.

### `main` (Atlantic / A2) vs `iridium` (A1 freezed-rtl) — the FSM *state graph
and both triggers are unchanged*; deltas are width/plumbing:

| Aspect | `iridium` (A1) | `main` (Atlantic/A2) |
|--------|----------------|----------------------|
| ICCM/DCCM depth | **Single shared** `PPW_FW_SRAM_ADDR_WDT` / `PPW_FW_SRAM_DPT` (ICCM=DCCM) | **Independent** `PPW_FW_{ICCM,DCCM}_SRAM_ADDR_WDT` / `PPW_FW_{ICCM,DCCM}_SRAM_DPT` |
| PRST copy size load | **Once** at `IDLE→CFG_RD`, full depth `{1'b1,0}`; `en=(ps==IDLE)&(ns!=IDLE)` | **Per-phase**: `ns==PRST_DCCM`→DCCM_DPT, `ns==PRST_ICCM`→ICCM_DPT (SW→csr size); `en` fires on each of PRST_DCCM/ICCM/SW_DCCM/SW_ICCM entry (needed since ICCM≠DCCM depth) |
| last-addr load | once, all-ones | per-phase, `depth-1` per CCM |
| Multi-PHY | hardcoded | `parameter NUM_OF_PHY=1`; `ppw_ccm_fast_access_addr` width `[NUM_OF_PHY-1:0]` |
| AXI sideband | no VRFID | **VRFID** (VF routing id, `MEM_VRFID_WDT`) threaded on aw/ar/b/r of the MNG AXI — A2 VF/virtualization initiative |
| `el_axi2pipe_if` | no meta ports | adds `e_meta_data`/`w_meta_data` (tied off for MNG) |
| `sram_copy_addr_staus` | fixed `[13:0]` | parametric `[PPW_FW_SRAM_ADDR_WDT-1:0]` |

Net: A2 didn't change *how* the copy is triggered or configured — it split ICCM
and DCCM into independently-sized memories (so the size/last-addr registers now
reload between the DCCM and ICCM phases) and threaded VF/VRFID + memory
meta-data through the datapath.

## Sources
- RTL: `Elmnt-Internal/ip.pcie.rtl` → `piw_macro/rtl/fw_sram_copy_handler.v`
  on **`main`** (Atlantic) and **`iridium`** (A1 freezed-rtl). Key symbols:
  `sram_copy_fsm_t`, FSM comb `case(sram_copy_fsm_ps)`, `prst_pending_start`,
  `prst_de_assert`, `sram_copy_prst_abort`, `csr_cfg_sram_sw_copy_start`,
  `csr_sts_sram_sw_copy_done`, `sram_copy_size_ns`, `sram_copy_last_addr_ns`,
  `sram_copy_from_rom`.
- Related KB: `sw-sram-copy-from-rom.md` (CSR offsets/fields for both paths,
  BOOT_FROM_ROM, dest base addresses), `phy-reset.md` (PERST re-copy trigger),
  `prstn-psoc-fuse-masked.md` (PERST edge reaching PSOC/EP).
