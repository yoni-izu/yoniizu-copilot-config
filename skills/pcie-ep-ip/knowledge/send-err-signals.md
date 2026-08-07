# cfg_send_cor_err / cfg_send_nf_err / cfg_send_f_err — "Sent Error" SII signals

- **Recorded:** 2026-08-05
- **Status:** Verified from doc
- **Tags:** send_cor_err, send_nf_err, send_f_err, cfg_send_cor_err, cfg_send_nf_err, cfg_send_f_err, err_cor, err_nonfatal, err_fatal, sii, general messaging reception, error message, aux_clk_g, cx_nfunc, per-function, sideband, uncorrectable error message, aer signaling

## Scope / Question
What do the SEND_COR_ERR / SEND_F_ERR / SEND_NF_ERR "interrupts" stand for?

## Findings
They are **DWC PCIe controller SII output signals** (full names `cfg_send_cor_err`,
`cfg_send_nf_err`, `cfg_send_f_err`), not interrupts and not CDM/DBI registers. They live in
the **SII "General Messaging Reception Signals"** group (Table 2-136), alongside
`radm_vendor_msg`, `radm_slot_pwr_limit`, `cfg_br_ctrl_serren`, etc.

- **Vector width:** `[(CX_NFUNC-1):0]` — one bit **per function** (PF/VF).
- **Direction / clock:** Output (`O`), synchronous to `aux_clk_g`, power domain `PD_VAUX`,
  active-high, one-shot pulse. `Exists: Always`.
- **Purpose:** They flag that the controller has **sent** the corresponding PCIe error message
  toward the Root Complex / upstream, i.e. they are the sideband indication that an
  `ERR_COR` / `ERR_NONFATAL` / `ERR_FATAL` message was generated for a received-TLP error.

Per-signal (EP/DM device semantics — this IP is an EP):
- **`cfg_send_cor_err`** = **Sent Correctable Error**. Controller has sent a message to the RC
  indicating an Rx TLP contained an error that **can be corrected** (maps to `ERR_COR`).
- **`cfg_send_nf_err`** = **Sent Non-Fatal Error**. Controller has sent a message to the RC
  indicating an Rx TLP contained a **non-fatal, uncorrectable** error (maps to `ERR_NONFATAL`).
- **`cfg_send_f_err`** = **Sent Fatal Error**. Controller has sent a message to the RC
  indicating an Rx TLP contained a **fatal, uncorrectable** error (maps to `ERR_FATAL`).

Role differences (same signals, other configs):
- **[RP]** "Send …Error": asserted when the RP *received* a TLP with that error class, so the
  RC can report it to host SW.
- **[SW]** "Send …Error": switch detected the error on its receive path (downstream→upstream
  port reporting).

Note the direction of reporting for EP: these are **egress/host-facing** AER message
indications (device → host). This is distinct from the *local-FW* AER notification path via the
PIW/PEP-wrapper SEI sideband `cfg_*_err_sts` — see `aer-fw-notification-routing.md`.

## RTL: yes, all three drive local FW interrupts (pulse)
In `pep_macro/rtl/pcie_iip_device_wrap.v` (which instantiates the PIW IIP device), each
`cfg_send_*_err` pulse is wired straight into an interrupt input of the PIW interrupt aggregator:

| SII signal | Interrupt port (PIW IIP) | Tree |
|------------|--------------------------|------|
| `cfg_send_cor_err` | `intr_spi_cfg_send_cor_err_pulse_intr` | **SPI** (shared peripheral intr) |
| `cfg_send_nf_err`  | `intr_sei_cfg_send_nf_err_pulse_intr`  | **SEI** (system error intr) |
| `cfg_send_f_err`   | `intr_sei_cfg_send_f_err_pulse_intr`   | **SEI** |

So there **is** a "message was sent" interrupt for each class. Severity split:
**correctable → SPI**, **non-fatal & fatal → SEI**. All are `*_pulse_intr` (edge/pulse-triggered,
matching the one-cycle pulse nature of the SII signals). This is a separate hook from the
`cfg_*_err_sts` status sideband — these fire on the *egress* AER message being emitted, i.e. FW
can be notified whenever the EP sent an `ERR_COR`/`ERR_NONFATAL`/`ERR_FATAL` upstream.

Signal path: DWC controller (`elmnt_u_core_top.sv`) → `pep_wrap.v` → `pcie_iip_device_wrap.v`
(intr mapping) → PIW IIP (`piw_iip_device_top.v` takes `cfg_send_*_err` as inputs). SDC constrains
them on `muxd_aux_clk_v` (`piw_macro/misc/piw_macro_sg.sdc`).

## Sources
- Doc: `controller/DWC_pcie_ctl_reference.pdf` (v6.42a-lca01) — Table 2-136 "SII: General
  Messaging Reception Signals", pp. 1712–1714 (`cfg_send_cor_err`, `cfg_send_nf_err`,
  `cfg_send_f_err`).
- RTL: `Elmnt-Internal/ip.pcie.rtl` → `pep_macro/rtl/pcie_iip_device_wrap.v`
  (`intr_spi_cfg_send_cor_err_pulse_intr`, `intr_sei_cfg_send_nf_err_pulse_intr`,
  `intr_sei_cfg_send_f_err_pulse_intr`); ports declared in `piw_macro/rtl/piw_iip_device_top.v`;
  wrapper micro-arch `pep_macro/misc/ELEMENT_LABS_EP_SUBSYS_WRAP_Micro_Architecture.md`.
