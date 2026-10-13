# PIW MEM downstream transaction aggregation (512B coalescing)

**Status:** verified · **Date:** 2026-10-12 · **Project:** A1 (Iridium), same layout in A2 CSR v2
**Tags:** piw, mem-fabric, aggregation, coalescing, gmem, throughput, mem_cfg, bring-up

## Summary

The PIW MEM path can aggregate/coalesce downstream (host→device) transactions up to
512B before forwarding them to GMEM. Without it, partial (<512B) accesses waste GMEM
bandwidth. **The feature is disabled at HW reset and the production FW never enables
it** — only the SV (validation) driver can turn it on, via a shell command.

## Mechanism (EP Spec Rev 2.0.0, §"Downstream Access Aggregation")

**Problem it solves:** GMEM supports only **full cache-line accesses** (e.g. 512B). The
device cache-line size is not necessarily correlated with the host's MPS / max read
request size, so the EP receives downstream TLPs that touch only *part* of a cache line.
Each such partial request still consumes the bandwidth of a **full cache-line memory
operation** — e.g. a 64B write burns a 512B GMEM op (1/8 efficiency).

**What the block does:** merges multiple downstream PCIe TLPs into a **single AXI burst**
before forwarding to the NoC. Rules:

- Aggregates only transactions targeting **contiguous addresses within one cache line**.
- The aggregated burst **must not cross a cache-line boundary**. An incoming TLP that
  would cross is *not* aggregated — it instead **triggers forwarding** of the current
  aggregate.
- A **configurable aggregation timeout** bounds accumulation latency. While the timer runs:
  - contiguous transactions accumulate into the aggregation buffer;
  - a **non-contiguous** transaction forwards the current aggregate, starts a new
    aggregation cycle and **resets the timer**;
  - on **timeout expiry** the accumulated transaction is forwarded **even if the cache
    line is not fully covered**.
- Applies to **both posted MWr and non-posted MRd**.
- Implementation note: HW aggregates **only transactions whose AXI size matches the bus
  width**.

**Completion handling (reads):** the single completion returned by the NoC for an
aggregated request is **internally split back into multiple PCIe completions**, one per
original downstream request, preserving the original request size and PCIe completion
semantics.

**Ordering:** the spec states the feature is **fully transparent to the PCIe link and does
not modify PCIe ordering, attributes, or correctness guarantees.** So there is no
doorbell-overtakes-payload hazard by design.

> The spec read is Rev 2.0.0 (the A2 document), but the A1 (`v1`) and A2 (`v2`) CSR
> headers carry **identical** `MEM_CFG` fields and the same `__RST 0x40401`, so the
> mechanism description applies to A1 as well.

## Register

`PIW_MAIN_CFG_MEM_MEM_CFG` — offset **0xBF0** in the PIW **main** config port
(`piw_main_regs`). Architect's absolute A1 address `0x1_0000_0000_408BF0`
⇒ PIW main base `0x1_0000_0000_408000`. SW_ACCESS = 0x2 (RW).

Path: `TOP.MNG.PCIE_EP_CLS.PIW_MNG.PIW_MAIN.MEM.MEM_CFG`

| Field | Bits | Mask | Reset |
|---|---|---|---|
| `RANGE_REG_TERMINATE_EN` | 0 | 0x1 | **1** |
| `DRAIN_FORK_TERM_EN` | 1 | 0x2 | 0 |
| `DRAIN_FORK_TERM_RD_WR_SEPERATE` | 2 | 0x4 | 0 |
| `WR_AGGREGATE_EN` | 3 | 0x8 | **0** |
| `RD_AGGREGATE_EN` | 4 | 0x10 | **0** |
| `WR_AGGREGATE_TIMER` | 15:8 | 0xFF00 | **0x04** |
| `RD_AGGREGATE_TIMER` | 23:16 | 0xFF0000 | **0x04** |

**Register reset value `__RST = 0x40401`** → aggregation OFF, both coalescing timers = 4.
To enable both: `0x40401 | 0x18 = 0x40419`.

## FW behaviour (A1, as of 2026-10)

- **Production path does NOT program MEM_CFG.** `el_piw.c` / `el_pcie_ep.c` touch only
  the DRAIN\*, RESP_MNPLT and SPLIT registers in the MEM block — never `MEM_CFG`.
  So the live value is the HW reset value and **aggregation is off by default**.
- The only RMW of `MEM_CFG` outside SV is absent; SV's `el_piw_sv.c` sets
  `RANGE_REG_TERMINATE_EN` (read-modify-write, preserves the aggregate bits, which are 0).
- SV API: `el_piw_sv_get_hbw_aggr()` / `el_piw_sv_set_hbw_aggr()`
  (`ip/pcie/ep/drivers/el_piw/el_piw_sv.c`, decl in `include/el_piw_sv.h`,
  struct `el_piw_hbw_aggr_cfg`).
- SV shell (`el_piw_shell_sv.c`):
  - `hbw_aggr_status` — dumps MEM_CFG + decoded EN/timer fields
  - `hbw_aggr_set <wr_en> <rd_en> [wr_timer] [rd_timer]` — timers 0x00–0xFF, optional
- `MEM_CFG` is in the PIW debug register dump (`el_piw_reg_dump.c`).
- Listed in the A1 protection-bits table `proj_infra/include/pb/a1_pb_pcie.txt`
  (`0xBF0 MEM_MEM_CFG  SG0 SG1`) — i.e. writable from SG0/SG1 security groups.

## Takeaway for bring-up

If aggregation is wanted by default, it must be added to the **PIW INIT** path in the
production driver (`el_piw.c`) — today it is a validation-only knob.

Ordering is **not** an open question (spec: transparent to PCIe ordering). The real open
items are:
- **Timer units and tuning** — reset `0x04`; the CSR description is `TBD` and the spec
  does not give units. The timer is the latency/efficiency knob: too short ⇒ aggregates
  rarely fill, too long ⇒ accumulation delay on partial traffic.
- **The AXI-size-matches-bus-width restriction** — determines which real host traffic
  patterns actually benefit; a workload whose TLPs don't meet it gains nothing.
- Whether to enable RD, WR, or both (read aggregation also costs host completion
  latency and outstanding-read credits).

## Citations

- `build_artifacts/include/v1/prototype/piw_main_cfg_regs.h:2932-2957` (fields)
- `build_artifacts/include/v1/prototype/piw_main_cfg_regs_attr.h:1346-1355` (`__RST 0x40401`)
- CSR source: `pcie_csr/yml/piw_macro/PIW_MEM_CFG.yml`
- `ip/pcie/ep/drivers/el_piw/el_piw_sv.c:2938-3030` (get/set HBW aggr)
- `ip/pcie/ep/drivers/el_piw/el_piw_shell_sv.c:1505-1610, 2655-2660` (shell cmds)
- `ip/pcie/ep/drivers/el_piw/el_piw.c` (no MEM_CFG access — production init)
