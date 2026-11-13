# A2 SE reset quiescence is not proven by current idle/ack signals

- **Recorded:** 2026-11-13
- **Status:** Partial — Neo A2 RTL trace; full integration and reset-owner contract remain unverified
- **Tags:** a2, scale-up, c2c, reset, quiescence, isolation, c2c_idle_status, early reset notify

## Scope / Question

Do A2 SE's idle-status and early-reset-notify signals prove that transactions and responses are drained before reset?

## Findings

- No. The three `C2C_IDLE_STATUS` indications cover selected credit-manager and DMT state, but do not establish that every transaction and response has completed; a DMT entry may be empty before a lookup response completes.
- The early reset-notify valid/ack handshake is not a drain-completion guarantee. Acknowledgement can represent reset notification/isolation or timeout.
- Do not use either indication alone as proof that reset is safe. Reset owners must define a cross-IP quiesce/isolation protocol, timeout and forced-isolation behavior. Full integration behavior remains unverified.

## Sources

- Neo A2 follow-up trace, `ip.se.rtl` mirror snapshot dated 2026-11-09; summarized in [su-ip knowledge: A2 SE reset](../../su-ip/knowledge/se-a2-reset-perst-hot-reset.md).
