# PCIe EP IP Knowledge Base — Index

This is the **expert memory** for the `pcie-ep-ip` skill. Every time the skill researches a
topic through the controller docs, PHY docs, EP spec, PIW docs, or RTL and verifies an answer,
it records the findings here so the next query on the same subject is fast and does not require
re-reading the (very large) source documents.

## How to use this file

1. **Before researching** any question, scan the table below (and full-text search the
   `knowledge/` directory) for relevant entries. If a matching topic exists, read its detail
   file first — it may answer the question directly or narrow the search.
2. **After verifying** a non-trivial finding from a doc or RTL, record it:
   - Create or update a topic file `knowledge/<short-topic-slug>.md` using the template below.
   - Add/refresh a row in the table (newest at top).
   - Always include citations (doc + page, RTL repo + file + symbol) so the finding can be
     re-verified and trusted.
3. **If a finding is later contradicted**, correct the topic file and note the change — keep
   the knowledge base accurate, not just append-only.

## Recorded Topics

<!-- Newest at top. Keep Tags lowercase and comma-separated for easy text search. -->

| Topic | File | Tags | Status | Recorded |
|-------|------|------|--------|----------|
| APP_CTRL — CRS hold-off + DRS level-permit; bring-up placement | [app-ctrl-crs-drs.md](app-ctrl-crs-drs.md) | app_ctrl, app_req_retry_en, app_drs_ready, crs, drs, hot reset, dl_down, dl_up, perst, bring-up, set_config_ready | Verified from FW + databook | 2026-06-30 |
| PHY CPU run/ack — multi-PHY parallel bring-up + timeout contract | [phy-cpu-run-ack.md](phy-cpu-run-ack.md) | phy cpu, cpu_run_req, cpu_run_ack, ppw_ctrl_cpu_ind, multi-phy, parallel bring-up, timeout, etimedout, k_busy_wait, vtable, el_pcie_phy | Verified from FW driver | 2026-06-30 |
| AXI Terminator STATUS regs are sticky — FW must write-clear after capture | [axi-term-status-sticky.md](axi-term-status-sticky.md) | axi termination, sticky status, AR_TERM_OCCURED, AW_TERM_OCCURED, csr_axi_termination_pkg, capture freeze, allow_log, fw clear | Verified from RTL + generated register headers | 2026-06-29 |
| EP PHY IDDQ / test_powerdown control (per-die) | [ep-phy-iddq-test-powerdown.md](ep-phy-iddq-test-powerdown.md) | iddq, test_powerdown, ppw_ctrl, phy_ctrl, reset default, die2, die3, firmware ownership | Verified from RTL + generated register headers | 2026-06-24 |
| PIW transaction drain (inbound only) | [piw-drain.md](piw-drain.md) | drain, piw, inbound, host-to-device, axi master, ruser, buser, sei, timeout, ctrl mng main, upstream termination | Verified from FW + doc | 2026-06-15 |
| PCIe6 PHY instance count per die (Iridium) | [phy-instance-count.md](phy-instance-count.md) | phy instances, per die, pcie ep, scale up, se, c2c, count | Verified from project DTS | 2026-06-14 |
| Downstream Termination (DS_TERM) datapath + host-side early clear ordering | [ds-term.md](ds-term.md) | ds_term, pep_ctrl_aux, elbi, mstr, rasdp, termination, host trapdoor, sys_init, pre_kernel_1, init priority, el_pcie_ep_host_clear_ds_term, host workaround | Verified from RTL + FW driver | 2026-06-30 |
| PHY reset signal chain | [phy-reset.md](phy-reset.md) | phy reset, perst, hot reset, phy_perst_n, phy_rst_n, psoc reset, fw sram copy | Verified from RTL | 2026-06-11 |

## Topic File Template

```markdown
# <Topic Title>

- **Recorded:** YYYY-MM-DD
- **Status:** Verified from RTL | Verified from doc | Partial / needs follow-up
- **Tags:** keyword, keyword, keyword

## Scope / Question
<what was asked / what this entry covers>

## Findings
<the verified facts — concise, durable, reusable>

## Sources
- Doc: `<file>` p.<n>
- RTL: `<repo>` → `<path>` (`<function/signal>`)
```
