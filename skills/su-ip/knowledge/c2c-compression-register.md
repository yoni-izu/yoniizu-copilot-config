# C2C Compression Register — EN gate, reset default, field map, FW enable state

- **Recorded:** 2026-06-29 (FW-integration state + per-version offsets added 2026-11-04)
- **Status:** Verified from RTL + generated regs (attr) + A1 FW (`a1_power_on_004` checkout)
- **Tags:** compression, c2c_compression, en bit, alg_disable, core_dis, cp bit mask, reset default, scale up, se driver, el_c2c_set_compression, is compression enabled, shell, set c2c compression, init, kconfig, dts

## Scope / Question
What does `C2C_COMPRESSION` (0x18c) control, is bit0 `EN` the master enable, what is its reset
default, and is a RMW-only-EN toggle (FW `el_c2c_set_compression`) correct?
**Is compression enabled** on a booted system — does the FW turn it on anywhere?

## Answer in one line
**No — C2C compression is OFF unless someone runs `set c2c compression on` from the shell.**
HW reset leaves `EN=0`, and the FW never writes the register during init (no Kconfig/DT knob,
no orchestrator call); the only writer is the shell command.

## FW integration state (verified 2026-11-04, `fw/ip/se` in `a1_power_on_004`)
- **Only writer** of `C2C_COMPRESSION` in the whole FW tree (excl. tests/venv/build_artifacts/zephyr):
  `el_c2c_set_compression_impl()` (`drivers/c2c/el_c2c.c:659`) — RMW of `EN_MSK` only, exposed
  as the `.set_compression` API op (`:1642`) / `el_c2c_set_compression()` syscall (`include/el_c2c_api.h:984`).
- **Only caller**: shell `set c2c compression <on|off> [port] [die]` →
  `cmd_c2c_compression_enable()` (`drivers/c2c/el_c2c_shell.c:299`, registered `:1929`).
  Port omitted → all ports; die omitted → all `EL_GLOB_NUM_DIES`. Accepts `on|off|1|0`.
- **No init-time enable**: `el_se_orch`, `el_se.c`, `el_c2c.c` init paths never touch it.
  No `CONFIG_*`/Kconfig or DT property for C2C compression (the only `enable-compression`
  DT prop in the tree is D2D's `el,d2d-manager.yaml` — different IP).
- **Tests codify "off after init"**: `test_c2c_reset_defaults` (`drivers/c2c/tests/host_tests_sv.c`
  + `arm_tests_sv.c`) asserts `C2C_COMPRESSION == C2C_COMPRESSION__RST` (0x6) on every port
  after the driver is ready. Adding an init-time enable would break this test.
- **No read-back shell cmd** (`show c2c` has only counters/ebr/traffic_gen). Verify live via a
  generic register read of `C2C_COMPRESSION` (bit0) at the port's C2C block.
- API doc comment (`el_c2c_api.h:977`) correctly says "Compression is disabled by default".

## Register offset per CSR version (reset 0x6 and `EN_MSK 0x1` identical in all)
| Project / CSR ver | `C2C_COMPRESSION` offset |
|---|---|
| A1 `ips/se/v1/3_0_11_3p0_1389` | `0x18c` |
| A2 `ips/se/v2/0_8_4_0p8_238`, `0_9_3_0p9_362` | `0x320` |
| A2 `ips/se/v2/1_0_1_1p1_695` | `0x20` |

## Findings
- **Field map** (`c2c_regs.h`): `EN` bit0; `AXI_SLV_PIPE_CP_BIT_MASK` bit1; `AXI_MSTR_PIPE_CP_BIT_MASK` bit2; `CORE_DIS` bit3; `ALG_DISABLE` bits16-31 (per-algorithm: vlc/mcv1-3 × bf16/fp32/int16/int32).
- **Reset = 0x6** (`c2c_regs_attr.h: C2C_COMPRESSION__RST 0x6` = 0b0110): **EN=0 (compression DISABLED at reset)**, CP-bit-masks bit1/bit2=1, CORE_DIS=0, ALG_DISABLE=0. `EN_HW_ACCESS=0` → SW-only RW; HW never changes it.
- **EN is the master gate.** `compression_en_data_r` gates both pipes: `master_w_comp_send_to_comp = compression_en_data_r & (compression_in_en | ~empty)`; `read_comp_en_int = compression_en_data_r & ~np_active`; TLP fmtrs size payload by it. So bit0=1 enables, 0 disables. RMW toggling only EN is correct.
- **CP-mask bits (1/2)** only matter when EN=0 (force cp indication off); leaving them at reset=1 is correct. **CORE_DIS / ALG_DISABLE** are tuning; FW leaves at reset → fine.
- Compression = hardware lossless, **posted MemWr only**, ~20% BW gain, 7cc enc / 3cc dec. "Compression disabled" in PCIe-compliant mode (spec P298/P312) is mode-specific, not the global default.
- **FW gotcha:** documenting compression as "enabled by default" is WRONG — reset EN=0.

## Sources
- RTL: `ip.se.rtl` → `c2c_macro/rtl/c2c_axi_master_pipe_compression.v`, `c2c_axi_slave_pipe_compression.v`, `c2c_compression_top.v` (`compression_en_data_r`, `cfg_compression_dis`, `compression_alg_disable_data_r`)
- Regs: `build_artifacts/include/ips/se/{v1,v2}/<ver>/prototype/c2c_regs.h` (fields/offset), `c2c_regs_attr.h` (`C2C_COMPRESSION__RST=0x6`)
- Spec: `PCIe C2C Rev 2.0.0.docx` p.30 §7.2.5, P524-533
- FW: `ip.se.sw.fw` → `drivers/c2c/el_c2c.c` (`el_c2c_set_compression_impl`), `drivers/c2c/el_c2c_shell.c` (`cmd_c2c_compression_enable`), `include/el_c2c_api.h` (`el_c2c_set_compression`), `drivers/c2c/tests/{host,arm}_tests_sv.c` (`c2c_ro_reset_checks` / `test_c2c_reset_defaults`)
