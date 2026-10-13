# Refclk requirement to exit LTSSM Detect.Active (receiver detection)

- **Recorded:** 2026-09-16
- **Status:** Verified from doc
- **Tags:** detect, detect.active, receiver detection, refclk, reference clock, pll lock,
  mpll off, p1, p1.cpm, txdetectrx, pipe_txx_detectrx, txn_detrx_req, ltssm exit, polling,
  power state, protocol definition

## Scope / Question
"By protocol definitions, is refclk needed to exit Detect.Active?" i.e. does the LTSSM need
the reference clock to complete receiver detection and move Detect.Active → Polling.

## Findings
- **Yes — refclk (reference clock) is required; PLL/MPLL lock is NOT.**
- The operation that gates `Detect.Active → Polling` is **receiver detection**.
- Receiver detection is a **P1** PHY operation, driven over PIPE via `TxDetectRx/Loopback`
  (`pipe_txX_detectrx` → PHY `txN_detrx_req_i`), two-way handshake on `txN_ack_o` /
  `txN_detrx_result_o`. Databook: "can be initiated in the TX state (`txN_pstate_i`) of **P1**";
  `txN_detrx_req_i` **must stay de-asserted in P0/P0s**. PIPE detect signals are **PCLK-sync**
  (PCLK derived from refclk).
- In **plain P1** the **MPLL/PLL can be powered down** (`txN_pll_en_i=0`) → detection does not
  need PLL lock. But **refclk is still running** in P1.
- Refclk is only gated by **P1 → P1.CPM** (de-assert `ref_clk_en_i`, stop `ref_clk_o`), then
  P1.1/P1.2/P2. Receiver detection is not done in those refclk-gated substates.
- Consequence: no refclk ⇒ no PCLK ⇒ PHY cannot run the detect-rx handshake ⇒ LTSSM cannot
  leave Detect.Active. A device always needs *a* reference clock — even SRIS supplies its own.
- PLL lock matters later (Polling / bit-rate operation), not for clearing Detect.

## Sources
- Doc: `dwc_pcie6phy_g2_tsmc5ff_x4ns_databook.pdf` p.60 (§4.7 Receiver Detection),
  p.72–73 (§4.13 TX/RX Power States — P1 MPLL-off, P1.CPM refclk gating)
- Doc: `dwc_pcie6_phy_pcs_wrapper_databook.pdf` p.139 (`pipe_txX_detectrx`, PCLK-synchronous)
