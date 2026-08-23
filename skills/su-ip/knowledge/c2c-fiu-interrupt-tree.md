# C2C FIU SEI/SPI Interrupt Tree — Masking & SEI-Owned Drain Model

- **Recorded:** 2026-08-17
- **Status:** Verified from generated C2C regs + FIU driver contract + el_irq_tree walker semantics
- **Tags:** c2c, se, scale up, fiu, sei, spi, interrupt, cause, mask, masked_intr, w1c, el_irq_tree, fiu_mem, fiu_mng, isolation_drain, error handling, ipsesw-139

## Scope / Question
How is the C2C FIU (Fault Isolation Unit) error interrupt structured, and is the
"SEI-owned, SPI-stays-masked, one dispatch drains both leaves" FW model HW-correct?
(Raised validating ip.se.sw.fw PR #148 / IPSESW-139.)

## Findings

### C2C root interrupt register triplet (per interrupt class)
Each C2C interrupt class exposes the standard **CAUSE / MASK / MASKED_INTR** triplet
(generated `prototype/c2c_regs.h`, offsets relative to the C2C port base):

| Class | CAUSE | MASK | MASKED_INTR |
|-------|-------|------|-------------|
| SPI   | 0x324 | 0x328 | 0x330 |
| SEI   | 0x334 | 0x338 | 0x340 |

- **CAUSE** latches the raw pending bits **regardless of MASK**.
- **MASK**: `1 = masked` (the PR's `keep_masked` bitmask writes 1s here).
- **MASKED_INTR** = `CAUSE & ~MASK` — this is what drives the CPU interrupt line.

Consequence (verified basis for the PR's design): masking the SPI FIU bits suppresses
the CPU line via MASKED_INTR **but the SPI CAUSE bit still latches**. A poll-based
monitor (`el_irq_mon`) that reads CAUSE directly still sees and dispatches the bit —
masking does **not** hide it from the poll path.

### FIU bit positions (identical in SEI and SPI roots)
- `FIU_MEM` = **bit 0** (mask 0x1)
- `FIU_MNG` = **bit 1** (mask 0x2)
- (SEI root also: `ISOLATION_DRAIN_AGG` bit 5 / 0x20, `DM_CORE_SEI` bit 13, `DM_AUX` bit 14 — aggregator child nodes.)

So an FW binding table keyed to bit 0 (`C2C_FIU_COOKIE(..., 0, EL_FIU_PORT_MEM)`) on
both the SEI and SPI roots correctly targets the FIU-MEM summary in each.

### SEI-owned drain model (verified correct)
The FIU contract (`ip.noc` `el_fiu_intr.h` / `el_fiu_group.c
el_fiu_mgr_handle_error_context`): a single **SEI** call reads **both** the FIU SEI
and SPI leaf causes and **W1C-clears both** in one shot; "FIU SPI is never unmasked as
an interrupt source — SEI is the single dispatch signal, SPI is read as latched context."
`num_err_rsp_events` / `err_rsp[]` are filled from the driver's own buffers **independent
of** the optional raw `error_info`/`sei_error_info` sinks (so a caller may leave those
NULL to avoid the ~580 B `el_fiu_intr_event` decode structs on the ISR stack).

### Why binding the same handler on both SEI[0] and SPI[0] is safe (walker ordering)
`el_irq_tree` walker (`ip.infra` `el_irq_tree.c walk_tree`) uses **dispatch-then-clear
(post-order W1C)**: it dispatches every bit's handler in a node, *then* `w1c_and_retry`
writes CAUSE back and re-reads; if bits re-assert it re-scans (up to `DEFAULT_RETRY`=20)
and resets the accumulated rc. Because the FIU handler drains the leaf **before** the
walker W1Cs the C2C root summary, the level-driven summary has already de-asserted, so
the W1C sticks with no retry storm. Whichever root (SEI CPU-IRQ path or SPI poll path)
is walked first drains the pair; the second dispatch reads 0 leaves → `handled=false` →
returns 0 → its root W1C clears cleanly. Idempotent, order-independent. **Verified HW-correct.**

### el_irq_tree return-code semantics (relevant to handler return values)
`accum_rc`: once a frame's `rc < 0` it's sticky (further results ignored); positive
results are summed. **Important nuance:** a handler returning negative does **not skip**
sibling handlers — all bits are still dispatched — it only poisons the *aggregate count*
(frame rc) for that cause register. So a handler that returns `-ENOTSUP`/`-EIO` corrupts
the reported handled-count, not the servicing. FW handlers on the summing walker should
return `0` (not a negative) for "nothing to do / not my config" cases to keep the count
meaningful; reserve negative for genuine failures worth surfacing.

## Sources
- Regs: `fw/build_artifacts/include/prototype/c2c_regs.h` — SEI 0x334/0x338/0x340, SPI 0x324/0x328/0x330; `*_CAUSE__FIU_MEM` bit0, `*_FIU_MNG` bit1, `*_ISOLATION_DRAIN_AGG` bit5.
- FIU contract: `fw/ip/noc/include/el_fiu_intr.h` (SEI-owned, SPI-masked), `fw/ip/noc/drivers/el_fiu/el_fiu_group.c` (`el_fiu_mgr_handle_error_context`: reads+W1C both leaves; `num_err_rsp_events` independent of sinks).
- Walker: `fw/infra/drivers/el_irq_tree/el_irq_tree.c` (`walk_tree` post-order `w1c_and_retry`, `accum_rc` sticky-negative).
- FW under review: `ip.se.sw.fw` PR #148 / IPSESW-139 (`drivers/c2c/el_c2c_irq_handlers.c`, `el_c2c_cause_descs.c`, `el_c2c.c`).
