# C2C Compression Register — EN gate, reset default, field map

- **Recorded:** 2026-06-29
- **Status:** Verified from RTL + generated regs (attr)
- **Tags:** compression, c2c_compression, en bit, alg_disable, core_dis, cp bit mask, reset default, scale up, se driver, el_c2c_set_compression

## Scope / Question
What does `C2C_COMPRESSION` (0x18c) control, is bit0 `EN` the master enable, what is its reset
default, and is a RMW-only-EN toggle (FW `el_c2c_set_compression`) correct?

## Findings
- **Field map** (`c2c_regs.h`): `EN` bit0; `AXI_SLV_PIPE_CP_BIT_MASK` bit1; `AXI_MSTR_PIPE_CP_BIT_MASK` bit2; `CORE_DIS` bit3; `ALG_DISABLE` bits16-31 (per-algorithm: vlc/mcv1-3 × bf16/fp32/int16/int32).
- **Reset = 0x6** (`c2c_regs_attr.h: C2C_COMPRESSION__RST 0x6` = 0b0110): **EN=0 (compression DISABLED at reset)**, CP-bit-masks bit1/bit2=1, CORE_DIS=0, ALG_DISABLE=0. `EN_HW_ACCESS=0` → SW-only RW; HW never changes it.
- **EN is the master gate.** `compression_en_data_r` gates both pipes: `master_w_comp_send_to_comp = compression_en_data_r & (compression_in_en | ~empty)`; `read_comp_en_int = compression_en_data_r & ~np_active`; TLP fmtrs size payload by it. So bit0=1 enables, 0 disables. RMW toggling only EN is correct.
- **CP-mask bits (1/2)** only matter when EN=0 (force cp indication off); leaving them at reset=1 is correct. **CORE_DIS / ALG_DISABLE** are tuning; FW leaves at reset → fine.
- Compression = hardware lossless, **posted MemWr only**, ~20% BW gain, 7cc enc / 3cc dec. "Compression disabled" in PCIe-compliant mode (spec P298/P312) is mode-specific, not the global default.
- **FW gotcha:** documenting compression as "enabled by default" is WRONG — reset EN=0.

## Sources
- RTL: `ip.se.rtl` → `c2c_macro/rtl/c2c_axi_master_pipe_compression.v`, `c2c_axi_slave_pipe_compression.v`, `c2c_compression_top.v` (`compression_en_data_r`, `cfg_compression_dis`, `compression_alg_disable_data_r`)
- Regs: `build_artifacts/include/prototype/c2c_regs.h` (fields), `c2c_regs_attr.h` (`C2C_COMPRESSION__RST=0x6`)
- Spec: `PCIe C2C Rev 2.0.0.docx` p.30 §7.2.5, P524-533
