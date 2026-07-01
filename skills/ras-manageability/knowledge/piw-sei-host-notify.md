# PIW SEI host-notify path — execution context + plugin-registration liveness gate

- **Recorded:** 2026-06-30
- **Status:** Verified from FW driver + design docs (`ip.pcie.sw.fw.ep`#272 / `IPPCIESW-313`)
- **Tags:** sei, piw, host notify, error handler, el_piw_err_handler_api, register_err_handler, irq tree, irq_mon, hard isr, mailbox, eq, el_pcie_sei_op_code, el_pcie_sei_raw_capture, el_error_handler, plugin, pcie_sei

## Scope / Question
The PCIe EP error-handling chain (PIW SEI → host notification) has two non-obvious operational
constraints that frequently bite integrators. What execution context is the host-notify path
safe in, and what makes the whole chain operationally live (vs present-but-no-op)?

## Findings

### 1. HOST_NOTIFY can block — bind via the poll-thread, never hard ISR
The `el_piw` action engine fans each SEI bit out into a fixed dispatch order
**RECOVER → LOG → HOST_NOTIFY**. The HOST_NOTIFY workers ultimately push events through the
**EQ mailbox**, which involves **allocation + a retry path** — both can **block**.

Therefore the IRQ-tree root that feeds these actions **must** be bound via the poll-thread:

- ✅ `EL_IRQ_TREE_ROOT_IRQ_MON_REGISTER` (el_irq_mon poll-thread context) — safe.
- ❌ `EL_IRQ_TREE_IRQ_CONNECT` (hard ISR context) — works in benign cases but will
  deadlock or stall on any mailbox-full path.

Today the only enforcement is the design doc
(`fw/ip/pcie/docs/pcie-piw-irq-actions.md`, "Execution context"). Recommended hardening:
- `BUILD_ASSERT` at the binding site naming the only allowed registration macro, **or**
- a runtime `__ASSERT(!k_is_in_isr(), ...)` at the entry to any worker that can call into the
  mailbox.

### 2. Plugin registration is the liveness gate, not the API plumbing
The `el_piw` driver publishes:
- `el_piw_err_handler_api { register_err_handler, unmask_interrupts }` as the device API;
- `el_piw_sei_notify(...)` which invokes `data->err_cb` — the registered callback.

**But until a consumer calls `el_piw_sei_register_err_handler(...)`, the entire HOST_NOTIFY
chain is dead code.** The workers run, the dispatcher fires, `el_piw_sei_notify` is reached,
and `err_cb` is `NULL`. By default there is **no log line** about a missing handler — the path
is silently inert.

The matching consumer is the `pcie_sei` plugin under
`ip/mng/subsystems/el_error_handler/plugins/`.

**Review/integration heuristic:** "is `pcie_sei.c` present in this build's
`el_error_handler/plugins/`?" If not, no PCIe errors reach the host regardless of how complete
the IIP / IIP_AUX / PIW wiring looks. Treat plugin presence as part of the integration
contract, not a separate concern.

### Capture-surface contract
The 5 capture mechanisms are enumerated in `enum el_pcie_sei_op_code`:

| Op code | Source | Captures |
|---------|--------|----------|
| `RESP_ERR` | AXI-split / resp-manip | AXI response error fields |
| `AXI_TERM` | terminator capture (PIW + EP variants) | AR/AW address, id, user (sticky) |
| `DRAIN_INTR` | drain block | drain status |
| `PULSE` | cause-identity only | bit identity, no payload |
| `CPL_TIMEOUT` | radm completion timeout (via `dwc_ep_ctl` from `PEP_CTRL_CORE`) | timeout context |

`struct el_pcie_sei_raw_capture` is documented as an **FW-internal contract** between workers
and the plugin — **not** a host wire format. Host serialization (PLDM/MCTP payload, mailbox
routing) is the plugin's job. `mbox_dev` / `mbox_user_id` inside the capture are stamped by
`el_piw_sei_notify` between producer (worker) and consumer (plugin).

## Sources
- FW: `Elmnt-Internal/ip.pcie.sw.fw.ep` (PR #272 / `IPPCIESW-313`, head `5b49a11`):
  - `drivers/el_piw/el_piw_irq_actions.c` — `el_piw_irq_action_dispatch` (RECOVER→LOG→HOST_NOTIFY)
  - `drivers/el_piw/el_piw_irq_workers.c` — HOST_NOTIFY workers, mailbox send
  - `drivers/el_piw/el_piw.c` — `el_piw_err_handler_api`, `el_piw_sei_notify` (`data->err_cb`)
  - `include/el_pcie_sei.h` — `enum el_pcie_sei_op_code`, `struct el_pcie_sei_raw_capture`
- Docs: `fw/ip/pcie/docs/pcie-piw-irq-actions.md` (execution context),
  `fw/ip/pcie/docs/pcie-sei-irq-handler-howto.md`, `fw/ip/pcie/docs/pcie-sei-capture-spec.md`
- Consumer-side (separate stacked PR / plugin):
  `ip/mng/subsystems/el_error_handler/plugins/pcie_sei.c` (expected location)
- Related: `pcie-ep-ip` knowledge — `ds-term.md`, `axi-term-status-sticky.md`
