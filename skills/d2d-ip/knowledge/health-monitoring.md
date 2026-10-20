# D2D Link Health Monitoring

- **Recorded:** 2026-06-23
- **Status:** Verified from spec (Element D2D Specification Rev 2.0.0)
- **Tags:** health monitor, link health, eye monitoring, running parity, runtime link testing, ber, slm, silicon lifecycle, per-lane, retrain, autonomous bw, pll lock, ointerrupt_0, ointerrupt_1, d2d_err_link_test_ctl

## Scope / Question
What is the D2D link health monitoring feature? Layers, mechanisms, and how events reach FW.

## Findings
Health monitoring exists at two layers plus interrupt plumbing:

1. **Runtime Link Testing using Parity ("Running Parity")** — adapter/protocol layer (primary runtime mechanism).
   - Periodically injects parity bytes into data stream; RX checks/logs parity errors (P287).
   - Overhead ~0.1%: inserts 64*N bytes every 256*(256B)*N (P288).
   - Enabled via `D2D_ERR_LINK_TEST_CTL` `RX_EN`/`TX_EN`, in BOTH adapters; SW must trigger UCIe Link Retrain (P784-785, P289).
   - Negotiated over sideband (`Parity.Feature.Req`/`Parity.Feature.Ack`, P291).
   - Parity error = Correctable error → interrupt on `ointerrupt_1`; SW builds BER counter (P290, P786).

2. **PHY-level health monitoring** (UCIe 1.1 / SNPS PHY features, P723-727):
   - Silicon Lifecycle Management (SLM) for failure prediction & repair (P723).
   - Per-lane health monitoring (P724).
   - Mission-mode eye-diagram monitoring & repair (P725).
   - PLL lock monitoring via PLL test (P727).
   - UCIe 1.1 enhancement framing: "eye monitoring + trigger periodic link re-train" + per-lane error logs/counters w/ interrupt (P477, P480).

3. **Interrupts (how health reaches FW)** (P754-761):
   - `ointerrupt_0` link event: link status change (up<->down), HW autonomous BW change (P755-757).
   - `ointerrupt_1` link error: correctable / uncorr non-fatal / fatal incl. Running Parity correctable (P758-761, P786).

FW role: enable Running Parity in both adapters + trigger retrain; service correctable-error interrupts for BER; consume link-status/autonomous-BW + PHY SLM/eye/PLL.

## Sources

### Adapter combined-status classification (verified 2026-10-20)

GammaCORE32 MMR section 19.55, pages 1662-1663, defines the adapter
`reg_adapter_d2d_uncorr_err_stat_w` at 0xDC as a combined register despite
its name. Bits 0-5 are uncorrectable causes; bits 6-10 include CRC, adapter
retrain, correctable internal, correctable sideband, and running-parity
errors. Bit 11 is compliance status. A health classifier must mask the
intended severity class instead of treating every nonzero bit as
uncorrectable. A raw errstat dump can legitimately print the entire word.

The corresponding generated SE-independent AW D2D header agrees:
`aw_adapter_imp_spec_rdi_reg_regs.h`,
`REG_ADAPTER_D2D_UNCORR_ERR_STAT_W` fields, offset 0xDC.
The separate uncorrectable status register at 0x10 (MMR section 17.5,
pages 1524-1525) contains uncorrectable bits 0-5 only.
No hardware validation performed.

- Spec: `~/docs/D2D/D2D Specification Rev 2.0.0.docx`
  - Running Parity / runtime link testing: P286-291, P782-786 (Fig 6-10 / P285)
  - PHY health (SLM/per-lane/eye/PLL): P723-727; UCIe 1.1 framing P477, P480
  - Interrupts: P754-761
- Related KB: `error-injection.md`, `ucie-error-notification.md`
