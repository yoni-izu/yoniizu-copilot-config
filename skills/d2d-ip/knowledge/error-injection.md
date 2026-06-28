# D2D / UCIe Error Injection Mechanisms

- **Recorded:** 2026-06-23
- **Status:** Verified from spec (Element D2D Specification Rev 2.0.0)
- **Tags:** error injection, inject, crc, ecc, parity, prbs, link test, d2d_err_link_test_ctl, imp_mb_config6, imp_mb_config7, ber, runtime link testing, testability

## Scope / Question
Does D2D have an error-injection mechanism? What can be injected and how is it controlled?

## Findings
Yes — the D2D IP exposes several distinct error-injection / fault-insertion capabilities:

1. **CRC error injection (link/protocol path)**
   - HW can inject a CRC error via the `D2D_ERR_LINK_TEST_CTL` register (spec P1262).
   - Injected CRC correctable error is reflected in `D2D_CORR_ERR_STATUS` bit[0] (P1259).

2. **Runtime Link Testing using Parity ("Running Parity")** — the periodic-parity
   mechanism UCIe defines for runtime link-health/BER monitoring (P286–291, P782–786).
   - Adapter periodically injects parity bytes into the data stream; RX checks/logs them
     (64*N bytes every 256*(256B)*N ≈ 0.1% overhead).
   - Enabled per-direction via `D2D_ERR_LINK_TEST_CTL` `RX_EN` / `TX_EN` fields (P785),
     must be enabled in **both** adapters; SW must then trigger a **UCIe Link Retrain**.
   - Negotiated over sideband (`Parity.Feature.Req` / `Parity.Feature.Ack`, P291).
   - A parity error is treated as a **Correctable error**, raises an interrupt on
     `ointerrup_1` (P786); SW reads the parity error log to build a BER counter.

3. **ECC error injection (mailbox/wrapper path)**
   - `imp_mb_config6` / `imp_mb_config7` registers are used for **ECC error injection** (P1294).

4. **PHY-level testability (PRBS / loopback)** — supporting fault-insertion at the PHY:
   - Programmable PRBS with user-defined patterns (P721), internal & external loopback (P720),
     and a general "Error injection ability" listed among PHY features (P722).

5. **FLIT-level framing** — error injection at the FLIT/protocol layer is supported:
   - **CRC error injection** (item 1) operates on the FLIT CRC → effectively FLIT-level fault insertion (P1262).
   - **Periodic parity flit injection**: spec states "UCIe 1.0 included periodic parity flit injection" (P479), part of run-time testability (P477-480); this is the Running Parity mechanism (item 2).

## Sources
- Spec: `~/docs/D2D/D2D Specification Rev 2.0.0.docx`
  - CRC injection: P1262, status bit P1259
  - Running Parity / Runtime Link Testing: P286–291, P476–481, P782–786 (`D2D_ERR_LINK_TEST_CTL.RX_EN/TX_EN`, `ointerrup_1`)
  - ECC injection: P1294 (`imp_mb_config6/7`)
  - PHY testability (PRBS/loopback/error-injection): P720–722
- Related KB entry: `ucie-error-notification.md` (how injected errors are reported/escalated)
