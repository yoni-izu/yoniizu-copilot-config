# Bumping the PCIe PHY firmware drop (`external_phy_pcie_fw`)

**Status:** Verified from manifest + build framework + repo sources (workspace
`~/projects/iridium/a1_power_on_002/fw`, branch `a1_power_on_branch`).
**Recorded:** 2026-10-12

---

## TL;DR

The PHY FW version is **not written anywhere in the firmware source tree**. It is
carried end-to-end by **one `source=` URL in the repo manifest**, and every
downstream consumer derives the version from the **name of the directory inside
the extracted tarball**.

So a bump is:

1. one line in `psoc-fw-ci.xml` (a **manifest-repo** PR), and
2. **deleting the previous extracted drop** from `build_artifacts/`, which is a
   hard requirement, not hygiene — two separate consumers `FATAL_ERROR` if two
   version directories coexist.

---

## The one line

`ip.infra.sw.manifests` → `projects/iridium/<branch>/psoc-fw-ci.xml`:

```xml
<artifact name="external_phy_pcie_fw"
          extract_prefix="fw_external/pcie/phy/"
          dep_type="external"
          source="s3://s3-sw-external-artifacts/iridium/fw_external/PCIE/PHY/<VERSION>.tar.gz">
    <extract>
    </extract>
</artifact>
```

Only `<VERSION>` changes. `name`, `extract_prefix` and `dep_type` are stable.

- `dep_type="external"` ⇒ plain S3 tarball, **no `version=` attribute**, so it is
  *not* touched by the `update_manifest` / artifact-version-bump CI flow. It is
  only ever changed by a human editing this line.
- Precedent commit (same file, sibling artifact):
  `bbedd0017a` — *"[SW-0]: bump external_phy_d2d_fw to eland_pshaper_cal_1009_d0acef09 (a1_power_on_branch)"*,
  1 file changed, **1 insertion(+), 1 deletion(-)**. That is the whole shape of the change.
- Sibling artifact `external_phy_d2d_fw` (`fw_external/d2d/phy/`) follows the
  identical pattern — the machinery below is shared, not PCIe-specific.

> ⚠️ In a `repo`-managed workspace the file is checked out at
> `fw/.repo/manifests/projects/iridium/<branch>/psoc-fw-ci.xml`. That is a
> **checkout of `git@github.com:Elmnt-Internal/ip.infra.sw.manifests.git` (branch `main`)** —
> editing it locally only affects your tree. The real change is a PR on the
> manifests repo.

---

## The consumption chain

```
manifest  source=s3://.../PCIE/PHY/<VERSION>.tar.gz
    │  (repo sync / artifact fetch, extract_prefix="fw_external/pcie/phy/")
    ▼
build_artifacts/opt/atl/fw_external/pcie/phy/<VERSION>/
    ├── <name>.fw         + .sha256 + .size      ← ICCM (does NOT end in _dccm.fw)
    └── <name>_dccm.fw    + .sha256 + .size      ← DCCM
    │
    ├─► phy_fw_version.cmake      → pcie_phy_fw_version.h  (PCIE_PHY_FW_PACKAGE_VERSION)
    ├─► external_fw_versions.py   → -DPCIE_PHY_VER_STR / -DPCIE_PHY_<STEM>_SIZE
    └─► emit_pcie_phyfw.py        → piw_phyfw_{iccm,dccm}_{asic,onix,palladium}_fw.bin
```

### 1. `ip/pcie/phy/drivers/snps_pcie6_phy/phy_fw_version.cmake`

Globs `$ENV{WS_ROOT}/build_artifacts/opt/atl/fw_external/pcie/phy/*`, keeps
directories, and:

- **exactly 1** → `PCIE_PHY_FW_PACKAGE_VERSION = <dirname>`, written into the
  generated `pcie_phy_fw_version.h`.
- **0** → version left unset (driver falls back to `"Unavailable"`).
- **>1** → `FATAL_ERROR: Multiple external_phy_pcie_fw drops installed; resolve stale artifacts`.

Directory name must match `^[A-Za-z0-9_.-]+$` or it is a `FATAL_ERROR`.
Included from `snps_pcie6_phy/CMakeLists.txt`.

### 2. `build_framework/build_infra/pre_build_scripts/external_fw_versions.py`

Generic (all `fw_external/*` families). Walks `--root`, treats any directory
directly containing `*.fw|*.bin|*.hex|*.elf` as a *version dir*, and prints:

- **version macro** — artifact name = family path relative to root
  (`pcie/phy`) → `PCIE_PHY_VER_STR=<dirname>`.
- **size macro, one per payload file** — artifact name + **file stem**,
  sanitized/uppercased, `_SIZE` suffix. Current drop yields
  `PCIE_PHY_DWC_PCIE6_X4_NS_PCIE6V2_V_SIZE` and `..._V_DCCM_SIZE`.
  ⚠️ These names are **derived from the `.fw` filename**, so a vendor rename
  changes the macro names. (Checked: no C source references them by name today —
  the driver reads sizes at runtime — so a rename is currently harmless, but
  re-check before assuming.)
- Sizes come **only** from the shipped `<file>.size` sidecar (written by
  `package_artifacts.sh`), never from `stat`. Missing/malformed sidecar ⇒
  warning on stderr and **that one macro is silently dropped** — not a build
  failure. A silently-missing `*_SIZE` macro is the failure mode to watch for.

### 3. `ip/pcie/phy/scripts/emit_pcie_phyfw.py`

Converts the vendor **readmemh `.fw`** text into raw SoC flash images.
Hard constraints:

| Constraint | Value | Failure |
|---|---|---|
| version dirs under `FW_BASE` | exactly 1 | `"Expected exactly one PHY FW version directory … Clean stale drops or re-sync the 'external_phy_pcie_fw' artifact."` |
| `.fw` files per image | exactly 1 each | DCCM = `*_dccm.fw`; ICCM = any other `*.fw` |
| `VERSION_STRING_MAX` | `0x20 - 1` = **31 chars** | version string too long |
| `IMAGE_SIZE` | `0x10000` = **64 KB** per emitted image | image overflow |

Also parses `// FW =`, `// CONFIG MASTER =`, `// PROJECT =`, `// Generated on`
headers out of the `.fw` into a JSON provenance blob, and resolves flash
placement from `SPSRAM_PIW_PHYFW_{ICCM,DCCM}[_HI]` defines.

Wired in by `ip/pcie/phy/ip.pcie.sw.fw.phy.star` →
`fw_emit_pre_built_artifacts(... PHY_EMIT_VARIANTS=["asic","onix","palladium"] ...)`.

### 4. Runtime / reporting

`dwc_pcie6_phy_diag.c` prints **two independent** version readings:

- `"packaged (phy_fw_version.cmake)"` → `PCIE_PHY_FW_PACKAGE_VERSION` (directory name)
- `"artifact (PCIE_PHY_VER_STR)"` → the pre-build macro

plus `dwc_pcie6_phy.c` has `DWC_PCIE6_PHY_FW_VERSION_FROM_END 8U` — a runtime read
of an **8-byte version tail at the end of the ICCM image**, i.e. the *vendor's own*
version embedded in the blob. If the directory name and the tail disagree after a
bump, the tarball was mis-named upstream.

---

## Bump procedure

```bash
# 1. Edit the manifest line (PR on ip.infra.sw.manifests)
#    source=".../PCIE/PHY/<NEW_VERSION>.tar.gz"

# 2. MANDATORY: remove the previous extracted drop
rm -rf build_artifacts/opt/atl/fw_external/pcie/phy/<OLD_VERSION>

# 3. Re-sync the artifact (repo sync / octl ws sync), then
octl ws build ip/pcie/phy

# 4. Verify
grep -r 'PCIE_PHY_VER_STR\|PCIE_PHY_FW_PACKAGE_VERSION' <build dir>   # → <NEW_VERSION>
ls build_artifacts/opt/atl/fw_external/pcie/phy/                      # → exactly one dir
ls build_artifacts/x86_64/Release/bin/piw_phyfw_*_fw.bin              # → regenerated
```

Step 2 is the step a manifest-only edit misses. Skipping it produces **two**
hard failures (`phy_fw_version.cmake` and `emit_pcie_phyfw.py`), not a silent
wrong-version build — which is the good news.

### Tarball prerequisites (ask the PHY owner before merging)

- top-level dir named exactly `<VERSION>`, matching `^[A-Za-z0-9_.-]+$`, ≤ 31 chars
- exactly one non-`_dccm` `.fw` + exactly one `*_dccm.fw`
- `.size` **and** `.sha256` sidecars next to each `.fw`
- each emitted image ≤ 64 KB

---

## Cross-links / coupling

- **IPPCIESW-689 → [ep-link-speed-config.md](ep-link-speed-config.md)**: PHY FW
  **7.0.127 is unstable on the EP side at Gen6 on A1 silicon**. The accepted
  workaround is `link-speed = "gen5";` on both `ep_ctl0`/`ep_ctl1` in
  `proj_infra/soc/dts/a1/a1_hsi.dtsi` (PR `project.iridium.sw.fw.infra#1434`,
  label `PO-TEMP-ONLY`). **Any bump to 7.0.127 must land with that Gen5 cap already
  in place**, or EP link training is expected to be unstable.
- The vendored SDK at
  `ip/pcie/phy/drivers/snps_pcie6_phy/sdk/dwc_pcie6phy_g2_tsmc5ff_x4ns/2.63a_cust4_patch0c/`
  is a **separate** artifact-independent checkout (docs/regmap source). It is the
  only place the literal `2.63a_cust4_patch0c` appears in the source tree, and it
  does **not** move when the FW artifact is bumped.
