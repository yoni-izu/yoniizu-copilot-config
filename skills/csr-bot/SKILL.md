---
name: csr-bot
description: 'Answer register (CSR) questions for any project by resolving IP base addresses from project-bases and register offsets from IP CSR headers. Use when: register address, CSR lookup, what is the address of <REG>, base address of an IP, project bases, register offset, bitfield mask/shift, ip.<ip>.sw.csr, project_bases.h, a1/a2 variant register map, resolve absolute register address, which regmap does an IP use, decode a register define.'
argument-hint: 'Ask for a register/base — e.g. "a1 PCIe PEP_CTRL_CFG_OB_CLK_DIV address" or "list PCIe bases in a1"'
---

# CSR Bot — Register & Base Address Q&A

Resolve register information for **any project** and **any IP** by combining two
independent sources that live in `.csr` / `hw_defs` repos:

1. **Project bases** — the absolute base address of every IP *instance* in a
   given SoC variant (e.g. `a1`, `a2`).
2. **IP register offsets** — the offset of every register (and its bitfields)
   *within* an IP block.

> **Absolute register address = project base (instance) + register offset (within IP).**

## The Two-Layer Model

### Layer 1 — Project bases (per variant)

**True origin:** `project.<project>.sw.hw_defs` repo (e.g.
`project.iridium.sw.hw_defs`). Its `.star` declares the generated specs:
`project_bases.txt`, `project_bases.h`, `project_bases_fw.h`, plus
`memories.txt`, `address_space.txt`, `mock_ips.txt`, `soc_efuse.txt`.

**Where the generated file lands (fast path):**
```
<ws>/**/build_artifacts/include/<variant>/project/project_bases.h
<ws>/**/build_artifacts/include/<variant>/<version>/project_bases.h
```
Variants are directories like `a1/`, `a2/`. Each line maps an instance path to
an absolute base, and the line **above** each define is a
`// prototype:<regmap>` comment that names the IP register map providing that
instance's offsets:

```c
// prototype:pep_ctrl
#define MNG_PCIE_EP_CLS_PEP_MNG_PEP_CTRL_0     0x100000000440000
// prototype:dwc_pcie_usp
#define MNG_PCIE_EP_CLS_PEP_MNG_DBI_0          0x100000000442000
```

The `MNG_...` prefix encodes the routing/isolation path (which is why bases are
wide, ~56-bit values). Instance names end in `_<N>` for replicated blocks.

### Layer 2 — IP register offsets

**True origin:** `ip.<ip>.sw.csr` repo (e.g. `ip.pcie.sw.csr`,
`ip.infra.sw.csr`, `ip.mmu.sw.csr`, `ip.psoc.sw.csr`, `ip.vcore.sw.csr`, …).

Offsets live in `prototype/<block>_regs.h`. Each register define is the byte
offset within the block; bitfield helpers follow the `__MSK` / `__SFT` /
`__SIZE` / `__SW_ACCESS` convention:

```c
#define PEP_CTRL_CFG_OB_CLK_DIV                0x4
#define PEP_CTRL_CFG_OB_CLK_DIV__CORE_CLK_MSK  0x1c0
#define PEP_CTRL_CFG_OB_CLK_DIV__CORE_CLK_SFT  0x6
#define PEP_CTRL_CFG_OB_CLK_DIV__CORE_CLK_SIZE 0x3
```

Companion files in `prototype/`:
- `<block>_regs.h` — offsets + bitfield masks/shifts (**use this for math**).
- `<block>_regs_attr.h` — per-register attributes/metadata.
- `<block>_regs.lua` / `_attr.lua` — Lua mirrors (same data, for tooling).

The `// prototype:<regmap>` comment in `project_bases.h` maps 1:1 to
`prototype/<regmap>_regs.h` inside the matching `ip.<ip>.sw.csr` repo.

## How to Answer a Register Question

1. **Identify project, variant, IP, and register** from the user's question.
   Default variant is `a1` unless stated.
2. **Locate the bases file** for the variant (prefer the generated
   `build_artifacts` copy; fall back to the `hw_defs` repo):
   ```bash
   find ~/projects/*/* -maxdepth 6 -path '*build_artifacts/include/<variant>/*' \
        -name 'project_bases.h' -not -path '*/.git/*' 2>/dev/null
   ```
3. **Find the instance base** and read its `// prototype:<regmap>` tag:
   ```bash
   grep -nB1 -E '<INSTANCE_REGEX>' <project_bases.h>
   ```
4. **Locate the IP CSR repo** and the matching offset header:
   ```bash
   find ~/projects/*/* -maxdepth 4 -type d -name 'ip.<ip>.sw.csr' -not -path '*/.git/*'
   grep -E '^#define <REG> 0x' <ip.<ip>.sw.csr>/prototype/<regmap>_regs.h
   ```
5. **Compute** `abs = base + offset` and decode bitfields from `__MSK`/`__SFT`.
6. **Cite** both source files (base file + offset header) and show the arithmetic.

### Discovery cheatsheet

```bash
# List all IP CSR repos across every local workspace
find ~/projects/*/* -maxdepth 4 -type d -name 'ip.*.sw.csr' -not -path '*/.git/*'

# List project-bases (hw_defs) repos
find ~/projects/*/* -maxdepth 4 -type d -name 'project.*.sw.hw_defs' -not -path '*/.git/*'

# List available variants for a project
ls <ws>/**/build_artifacts/include/   # e.g. a1/ a2/ ...
```

## Dry Run — `a1` project bases + PCIe register offsets

**Question:** *What is the absolute address of `PEP_CTRL_CFG_OB_CLK_DIV` for the
management PCIe EP on the `a1` variant?*

**Step 1 — bases file (a1):**
`~/projects/iridium/six/fw/build_artifacts/include/a1/project/project_bases.h`
```c
// prototype:pep_ctrl
#define MNG_PCIE_EP_CLS_PEP_MNG_PEP_CTRL_0   0x100000000440000
```
→ instance base = `0x100000000440000`, regmap = `pep_ctrl`.

**Step 2 — offset header (PCIe IP CSR):**
`~/projects/iridium/four/ip-pcie-csr/ip.pcie.sw.csr/prototype/pep_ctrl_regs.h`
```c
#define PEP_CTRL_CFG_OB_CLK_DIV              0x4
```
→ register offset = `0x4`.

**Step 3 — resolve:**
```
0x100000000440000 + 0x4 = 0x100000000440004
```

**Bitfield decode (from the same header):**
| Field       | MSK     | SFT | SIZE |
|-------------|---------|-----|------|
| `CORE_CLK`  | `0x1c0` | `6` | 3    |
| `REF_CLK`   | `0x38`  | `3` | 3    |
| `PCIL_CLK`  | `0xe00` | `9` | 3    |

To read `CORE_CLK`: `(reg_val & 0x1c0) >> 6`.

**Other `a1` PCIe instance bases (same file) and their regmaps:**
| Instance                                   | Base                | Regmap header (`prototype/…_regs.h`) |
|--------------------------------------------|---------------------|--------------------------------------|
| `MNG_PCIE_EP_CLS_PIW_MNG_PIW_MAIN_0`       | `0x100000000408000` | `piw_main_cfg`                       |
| `MNG_PCIE_EP_CLS_PIW_MNG_PIW_CTRL_0`       | `0x10000000040b000` | `piw_ctrl_cfg`                       |
| `MNG_PCIE_EP_CLS_PIW_MNG_MSG_MAILBOX_0`    | `0x10000000040d000` | `piw_mailbox`                        |
| `MNG_PCIE_EP_CLS_PEP_MNG_PEP_CTRL_0`       | `0x100000000440000` | `pep_ctrl`                           |
| `MNG_PCIE_EP_CLS_PEP_MNG_PEP_CTRL_AUX_0`   | `0x100000000441000` | `pep_ctrl_aux`                       |
| `MNG_PCIE_EP_CLS_PEP_MNG_DBI_0`            | `0x100000000442000` | `dwc_pcie_usp`                       |
| `MNG_PCIE_EP_CLS_EP_PPW_0_PHY_CTRL_0`      | `0x100000000480000` | `el_regmap_pcie6`                    |
| `MNG_PCIE_EP_CLS_EP_PPW_0_PPW_CTRL_0`      | `0x100000000488000` | `ppw_ctrl`                           |

The `MNG_SE_TOP_<N>_DM_{USP,DSP}_DBI_0` entries are the scale-up (C2C) PCIe
dual-mode instances — same PCIe IP CSR repo, different project-bases instances.

## Notes & Caveats

- **`build_artifacts` is the fast, pre-resolved copy; `.csr` / `hw_defs` repos
  are the source of truth.** If a header looks stale or is missing, read the
  origin repo (or note that the workspace isn't fully synced — some repos here
  only contain a `.star` until synced).
- **Instance vs. block.** One IP block (e.g. `pep_ctrl`) can be instantiated
  many times; each instance has its own base line in `project_bases.h`. Offsets
  are identical across instances of the same block.
- **Variants matter.** `a1` and `a2` have different base maps. Always confirm
  the variant before quoting an absolute address.
- **Same IP, many projects.** The `ip.<ip>.sw.csr` repo is project-agnostic;
  only the project-bases layer changes per project/variant.
- **Versioned regmaps.** Some CSR repos carry `v1/` and `v2/<version>/prototype`
  trees. Match the version the project build pulls in if the user cares about a
  specific silicon rev; otherwise the top-level `prototype/` is the current one.
- Prefer `.h` offset headers for arithmetic; use `_attr.h` only when the user
  asks about register attributes/access policy beyond `__SW_ACCESS`.
