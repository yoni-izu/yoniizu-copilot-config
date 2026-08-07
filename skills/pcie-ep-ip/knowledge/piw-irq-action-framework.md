# PIW SEI per-bit action framework — LOG / RECOVER / HOST_NOTIFY semantics

- **Recorded:** 2026-07-27
- **Status:** Verified from FW
- **Tags:** piw, sei, irq tree, el_piw_irq_action_dispatch, el_piw_irq_action_ctx, action flags, recover, log, host notify, action ordering, isr context, poll thread, el_piw_irq_recover_axi_split, split_intr_clr, action classification

## Scope / Question
What does `EL_PIW_IRQ_ACTION_RECOVER` actually do, and what belongs under it?

## Findings

### Three composable per-bit actions, ROM descriptor, one generic dispatcher
Every serviced PIW SEI leaf cause bit gets an immutable `struct el_piw_irq_action_ctx`
descriptor in ROM, whose `actions` field ORs together:

```c
EL_PIW_IRQ_ACTION_LOG         = BIT(0)   /* severity-controlled, rate-limited line */
EL_PIW_IRQ_ACTION_RECOVER     = BIT(1)   /* per-bit hardware recovery worker */
EL_PIW_IRQ_ACTION_HOST_NOTIFY = BIT(2)   /* forward a structured event to the host driver */
```

`el_piw_irq_action_dispatch(dev, cookie)` is bound to every serviced bit through the IRQ-tree
binding cookie (cookie = pointer to that bit's ctx). It runs the enabled actions in a **fixed
order — RECOVER, then LOG, then HOST_NOTIFY** — rationale in-code: "Recovery runs first to
stabilize the hardware, then logging records the event, then host notification forwards it."
Return codes accumulate with the walker's sticky-negative / positive-sum convention
(`>0` took effect, `0` nothing to do, `<0` errno).

### RECOVER carries no built-in semantics — it is just a hook
`action_recover()` is `if (ctx->recover == NULL) return 0; return ctx->recover(dev, ctx);`.
The meaning is **conventional**, established by the workers:
- `el_piw_irq_recover_axi_split()` — reads the outstanding-split count, logs it, then writes
  `*_SPLIT_INTR_CLR` 1-then-0 ("clear register does not self-reset"). A PIW-owned latch,
  genuinely deasserted. **This is the canonical RECOVER.**
- `el_piw_irq_recover_drain()` — mostly reads and logs drained-transaction detail (looser fit).

**Classification rule:** RECOVER = touch HW we own to stabilize/deassert before the event is
logged and forwarded. Pure capture/count/log work does **not** belong under RECOVER. The action
set is explicitly extensible ("add a flag + a table entry to grow"), so a `COUNT` action next to
`LOG` is the right home for telemetry-style handlers (e.g. AER event counting — see
`aer-host-ownership-no-fw-clear.md`; wiring AER as RECOVER only "worked" because it W1C'd the
host-owned AER status).

### Unresolved: execution-context contradiction (pre-existing)
- `el_piw_irq_actions.h` header doc: "All actions execute in the caller's context
  (today: the IRQ-monitor poll thread)."
- `el_piw_irq_actions.c` dispatcher body: "This dispatcher runs in ISR context (invoked from the
  SEI/SPI IRQ-tree walker). TODO: the HOST_NOTIFY action can block on the EQ-mailbox send,
  which is not ISR-safe..."

Both cannot be true, and the answer decides what is legal inside a worker (blocking sends,
mutexes, long reads). The RAS KB entry `ras-manageability/knowledge/piw-sei-host-notify.md`
states HOST_NOTIFY must be bound via the poll thread
(`EL_IRQ_TREE_ROOT_IRQ_MON_REGISTER`), never hard ISR — so the header is likely the intended
truth and the dispatcher comment stale, but this is not yet confirmed per-binding.

## Sources
- FW: `ip.pcie.sw.fw.ep` → `include/el_piw_irq_actions.h` (`enum el_piw_irq_action_flag`,
  `struct el_piw_irq_action_ctx`, `el_piw_irq_action_dispatch` doc),
  `drivers/el_piw/el_piw_irq_actions.c` (`action_log`/`action_recover`/`action_host_notify`,
  `action_table[]` ordering, ISR-context TODO),
  `drivers/el_piw/el_piw_irq_workers.c` (`el_piw_irq_recover_axi_split`,
  `el_piw_irq_recover_drain`, `el_piw_irq_recover_aer`),
  `drivers/el_piw/el_piw_sei_tree.c` (per-bit ctx tables).
- Related KB: `aer-host-ownership-no-fw-clear.md`, `aer-handling-completeness.md`,
  `ras-manageability/knowledge/piw-sei-host-notify.md`.
