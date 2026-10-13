# Station Memoir

What happened on each setup — dated, 2–4 lines per entry, newest first, no transcripts.
Station facts (host, type, BMC, UART path) live in [stations.tsv](./stations.tsv)
(`pilot stations`), not here. Append with `pilot -t <station> memoir '<text>'`; promote
recurring lessons to [pitfalls.md](./pitfalls.md).

---

## Lab-wide facts (2026-10-04 — re-verify when stale)

- All `*-csr` hosts share `/users/home` (NFSv4.2 `soft`, `192.168.179.250:/homes`
  — srv17 mounts it from `192.168.181.250`) and `/validation`; kernel `6.17.0-38-generic`;
  `kernel.dmesg_restrict=0`; `sudo` needs a password; user is in `dialout`, `docker`,
  `project-iridium-validation-lab-sg`; `ipmitool`, `minicom`, `screen`, `curl`, `python3`
  present; `fwloader`/`fw_ctl` **not** on the host PATH.
- **Integration workspace** (latest-and-greatest of all SW components, the place to run
  `octl ws exec` star targets and company tools from):
  `~/projects/iridium/a1_power_on_branch/ws-integration-ci` — `integration-ci.xml`
  v`1.0.0-22092026-190101`, rev `a1_power_on_branch`, artifacts-only layout
  (`build_artifacts/opt/atl/{fw/scripts/{fw_ctl,fw_loader.py,host_lib,rpe,pcie_safe_reset,
  pldm,jtag,el_log_tools},device_fw,dkms,el-smi,el-qual,cpld,hw_system_tests,…}`), no
  source repos. A loose `spi_flash_image_arm_asic.bin` (16 MiB, 2026-10-04 14:13, sha256
  `9b6881e4…`) sits in its root — someone's working image, provenance unrecorded.
  Other workspaces on the share: `~/projects/iridium/main/ws-val-hw-system-tests-v2-ci`,
  `~/projects/iridium/one/fw` (`psoc-fw-ci`, a1_power_on_branch).
- `fw_ctl` in the lab is launched as `octl ws exec run_fw_ctl --args 'variant=asic
  interface=pcie'` → a docker container running `python3 -m fw_ctl.console` **as root**
  (seen on srv17); hence root-owned `/var/log/atl/fw_ctl/pcie_inventory.json`.

---

## Activity Log

### dsk61
- **2026-10-04 ~15:30Z** — Host went **down** while the pilot was being validated
  (`kex_exchange_identification: Connection reset by peer`, then no ping / `No route
  to host` from nuc74). Not caused by the pilot (observe-only all session). Re-probe
  when it is back; check whether the device/BDF and DKMS state survived.
- **2026-10-04** — Pilot birth probe (observe-only). 1 × `208b:0001` @ `03:00.0`, link
  details not captured. dmesg shows an earlier `el_pci` teardown at 09:59:59 with
  `Failed to send fini command` ×4, `Failed to send PSOC message, opcode 3, rc -5`,
  `PSOC hard reset during fini failed: -5`, then `device finalized` / `driver removed`
  — i.e. the FW channel was already dead when the driver was unloaded (not caused by
  the pilot). Evidence: inline only. Open: declare stand-alone vs riser; which NUC
  carries its UART.

### dsk70
- **2026-10-06 10:59Z** — Attempted first PCIe flash of ASIC image B (fpt_uuid 1334d13f..., deploy 06102026_102911_pcie_diag_shells_fullbuild) -- NOT completed. fw_ctl came up but pcie transport failed: BAR0 base unprogrammed (config 0x10 = 0c 00 00 00) after a failed FLR; dmesg showed 4 FLR cycles 09:05-09:47, two of them "giving up", so the board was being worked on that morning despite holders=none. Issued `sudo reboot` to re-assert PERST/reassign BARs; the ssh failed at TCP CONNECT (no session opened) so the reboot likely never ran -- host was already going down. Host 192.168.178.198:22 AND bmc 192.168.176.133 both unreachable since ~10:40 (dsk61 + els35 BMC reachable, so not a network issue). Bench appears to have lost standby power; needs physical/PDU check. Setup type still undeclared.
- **2026-10-04** — Pilot birth probe (observe-only). 1 × `208b:0001` @ `03:00.0`;
  SAT-allocated. Open: declare type; NUC mapping.

### nuc74
- **2026-10-04 16:04Z** — Integration workspace registered as the home for company tools: `~/projects/iridium/a1_power_on_branch/ws-integration-ci` (integration-ci v1.0.0-22092026-190101). `pilot ws` lists it (`*`), `pilot exec` defaults to it. Loose `spi_flash_image_arm_asic.bin` (sha256 9b6881e4…) in its root — provenance to confirm with Yoni.
- **2026-10-04 15:31Z** — Pilot tooling validated here: `pilot_probe.sh`,
  `uart_capture.sh` (5 s on `/dev/ttyACM0` @115200 → **0 bytes**, device silent / bench
  unknown), `pull_evidence.sh` → `~/.pilot/evidence/nuc74/04102026_153143_birth_probe/`.
- **2026-10-04** — Pilot birth probe (observe-only). Serial inventory recorded in the
  registry; `ttyACM0/1` idle at 9600 (unconfigured), no holders. Same day, `fw-deploy`
  verified two deployments from here on the shared home:
  `~/a1_power_on_images/04102026_144719_pcie_diag_no_reg_dump/asic` and
  `~/a1_power_on_images/04102026_150459_pcie_diag_readable_safe_regs/asic`
  (image + `CHANGE_NOTES.md`, sha256 OK). Open: which bench it serves.

### srv17
- **2026-10-04 21:15Z** — Phase 0 (link-stability experiments): fw_ctl 2.3.0 launches OK via the fixed `pilot exec`, but PCIe register access is DEAD tray-wide: "pcie transport bring-up failed: Access sanity check failed at 0x0100000000000000: BAR read 0xFFFFFFFF (PCIe completion timeout / BAR not routed)". Matches host sysfs showing all 7 enumerated OAMs at Unknown x63. Consequence: `bmc load --boot` (which needs the pcie iface for iface init + host_lib) cannot be used until the link is back; use the Redfish load + a BMC-side `bmccli device reset -d all` instead.
- **2026-10-04 15:50Z** — Rebooted ~15:46Z under another user (michael*, `octl ws exec run_fw_ctl` → root `fw_ctl.console`); came back **7/8** with a new BDF set `5b 5f 62 66 68 6b 6e`; `68:00.0` enumerates as **208b** while the other six are **1d0f** (mixed vendor IDs on one tray); only `68` downgraded (Gen5). Pre-reboot bad links (`66` 2.5G, `6e` x63) cleared by the reboot.
- **2026-10-04 15:30Z** — Probe re-run with sysfs link reading: two OAMs unhealthy
  (`66:00.0` at 2.5 GT/s, `6e:00.0` `Unknown x63`), one downgraded (`5d:00.0` Gen5),
  three at Gen6 x8. Not investigated (allocated to someone else).
- **2026-10-04** — Pilot birth probe (observe-only, allocated to `jayakrishnan.kb`).
  6/8 OAMs enumerated as `1d0f:7596`, clean `PCIe Endpoint` enumeration at 15:08:25,
  no KMD installed on the host. Open: OAM↔BDF map; why 2 OAMs are absent (not ours to
  chase while allocated).

### srv26 / srv25
- **2026-10-04** — Probe only: srv26 2/8 up (`5e`, `66`, `208b`), srv25 0/8. No BMC
  session attempted (password auth).

### els35
- **2026-10-04 22:36Z** — ELS35 GEN5 DOWNGRADE IS TRAY-WIDE, NOT JUST OUR EP (2026-10-05). pcieportprop shows SW0 upstream port 32 negotiated G5x16 against a G6x16 capability - the SWITCH OWN UPLINK TO THE HOST downgrades to Gen5, with no Element silicon involved in that link. Our one trained OAM sits on SW1 port 104 (bus 6Ch, phys slot 52) at G5x8 vs G6x8 capable. Meanwhile the two Mellanox CX8 NICs hold G6x16 cleanly on SW0 p112 and SW1 p112. So Gen6 x8 is marginal on the OAM/uplink paths while Gen6 x16 to the NICs is fine. Treat any Gen6->Gen5 fallback on an OAM as a candidate PLATFORM/signal-integrity issue before blaming FW.
- **2026-10-04 22:36Z** — ELS35 SWITCH PORT MAP (from pcieportprop, 2026-10-05). Each PEX90104 exposes DS ports 0,8,16,24,32,80,88,96,104,112 + one upstream. SW0 (us=32, bus 59h): p0->6Eh, p8->5Bh slot4, p16->5Ch slot8, p24->5Dh slot12, p80->5Eh slot40, p88->5Fh slot44, p96->60h slot48, p104->61h slot52, p112->62h slot56 = Mellanox CX8, p128->63h-6Dh = CASCADE to SW1. SW1 (us=128, bus 63h): p0->6Eh, p8->65h slot4, p16->66h slot8, p24->67h slot12, p32->68h slot16, p80->69h slot40, p88->6Ah slot44, p96->6Bh slot48, p104->6Ch slot52, p112->6Dh slot56 = Mellanox CX8. Topology is a CASCADE not a fan-out: host -> SW0 p32 -> SW0 p128 -> SW1 p128(us) -> SW1 DS ports. Use the "Rx Detect" column to tell a populated-but-not-training slot (Present) from an empty slot (Absent).
- **2026-10-04 22:35Z** — PCIe SWITCH CONSOLES (ELS tray, 2x Broadcom PEX90104). BMC ssh ports: 2208=SW0 (upstream port 32, bus 59h), 2209=SW1 (upstream port 128, bus 63h); 2210/2211 are SDB framed-binary, NOT text. Open with: export SSHPASS=$(cat ~/.pilot/els35.pw); sshpass -e ssh -tt -p 2208 root@192.168.177.222 . Prompt is "cmd>". CLI acts on CARRIAGE RETURN (\r) not \n. Identify which switch you are on with: pcieportprop host  (SW0 prints Host BitMask word1=0x1 -> port 32; SW1 prints word4=0x1 -> port 128).
- **2026-10-04 22:08Z** — D2D sideband init hang explains dead-shell OAMs (per Yoni): sideband interface init can get stuck and freeze boot, intermittently. Not PCIe-related. Reset the device to recover; no deeper triage needed until the D2D fix lands.
- **2026-10-04 22:02Z** — ROOT CAUSE (register-level proof, 2026-10-05): the PEP core SPI interrupt is MASKED, so FW link-down/hot-reset handling NEVER RUNS. Measured on all 6 responsive OAMs via `dwc_ep_ctl regs_dump PEP_CTRL_CORE`: INTR_SPI_MASK (pep_core+0xac) = 0x3f on ALL six = every one of the 6 cause bits masked (handler masks by SETTING the bit, so 1=masked). INTR_SPI_CAUSE (pep_core+0xa8) = 0x3f on five, 0x1f on oam2 = the events DID fire and are still latched/unserviced. Bit map: 0x1 SMLH_REQ_RST_N_FALL, 0x2 SMLH_REQ_RST_N_RISE, 0x4 LINK_REQ_RST_N_FALL, 0x8 LINK_REQ_RST_N_RISE, 0x10 RADM_SLOT_PWR_LIMIT, 0x20 RPLH_FLIT_ECC_CORR_ERR. (oam2 lacks 0x20 because it trained at Gen1 x8, no Flit mode - good consistency check.) CHAIN: nothing in dwc_ep_ctl.c ever unmasks INTR_SPI_MASK - it is only read-modify-written INSIDE dwc_ep_ctl_handle_spi_intr() itself => the handler can never be entered => handle_post_hot_reset() never runs => ltssm_en_set(dev,true) never re-arms APP_LTSSM_EN after HW clears it on core_rst_n (which HW does because DELAY_LINK_TRAINING=1) => LTSSM stuck in Detect.Quiet forever. LINK_REQ_RST_N_RISE (0x8) is pending RIGHT NOW on every device, un-serviced. CONSEQUENCE observed: after `sudo reboot` of srv17 the host enumerated ZERO 208b devices (lspci -d 208b: empty, was 7 earlier) because the EPs never trained. A host reboot CANNOT recover this; only a device-side reset (re-runs FW linkup) or writing APP_LTSSM_EN=1 by hand. FIX DIRECTION: unmask the reset-notification causes at init, AND add a reconciliation that re-asserts APP_LTSSM_EN whenever link is down and the bit reads 0, so recovery does not depend on a single edge-triggered IRQ.
- **2026-10-04 21:26Z** — ROOT CAUSE FOUND for the "PCIe link instability" on the ELS tray (2026-10-05, image 174227 / uuid 50340565..., all 8 OAMs). Symptom: tray-wide PCIe dead, host sees stale Unknown x63 + BAR reads 0xFFFFFFFF. UART bringup_dump showed on ALL 6 responsive OAMs, with zero variance: APP_LTSSM_EN (pep_core+0x30) = 0x0 and LINK_DN_CTRL (pep_core+0x40) = 0x202 i.e. DELAY_LINK_DN_RST=0, DELAY_LINK_TRAINING=1; link_status = SMLH DOWN / RDLH DOWN / LTSSM Detect.Quiet(0x00). PROOF: a single `devmem 0xf0440030 32 1` (the exact write ltssm_en_set() does) took the link straight to LTSSM L0 (0x11), SMLH+RDLH UP, Negotiated Gen6 x8. Reproduced 6/6 across the tray. So the PHY/electrical/host path is entirely healthy - the link was held down purely by the enable bit. MECHANISM (source-confirmed in dwc_ep_ctl.c): with DELAY_LINK_TRAINING=1 the HW clears app_ltssm_enable on core_rst_n, and the ONLY code that re-arms it is handle_post_hot_reset(), reachable only from the LINK_REQ_RST_N rising-edge SPI interrupt. Any link-down that is not a hot reset (surprise down, PHY loss, or an edge lost while the handler has the cause masked) leaves app_ltssm_enable=0 with nothing to re-arm it => link permanently dead in Detect.Quiet. This is a one-way trapdoor, not an electrical instability: it turns a single transient blip into a permanent dead link. NOTE: oam2 recovered to Gen1 x8 while the other five hit Gen6 x8 - incomplete speed change, track separately. oam1 and oam7 have NO live FW shell at all (no echo on UART) though BMC reports present=1 pwrgd=1; dev1 is boot=2 vs boot=3 for the rest.
- **2026-10-04 21:20Z** — OAM FW shell (UART, no PCIe needed) command set for link-stability work. dwc_ep_ctl subcommands: bringup_dump, link_status (SMLH/RDLH/LTSSM all controllers), ltssm_logger_dump, ltssm_logger_clear (preserves enable/masks/threshold), ec_enable <0|1>, ec_clear, ec_restart (clear+enable), ec_print_group <n> [lanes], ec_print_all [lanes], ras_des_dump, regs_dump [all|PEP_CTRL_CORE|PEP_CTRL_AUX|DBI|DBI.CDM|DBI.ATU] [off [len]]. el_pcie_ep subcommands: addr_map, bmu_dump, check_irq, downstream_termination 0|1, hw_map, iatu_dump, set_ownership 0|1. Top level also has: fw_version (reads FPT A/B/C headers straight from flash - the image-provenance check that needs NO PCIe), manifest_version, diag, device, reset, devmem, pwr_thrm. Read-and-clear delta sampling is therefore possible entirely over UART.
- **2026-10-04 20:21Z** — bmccli surveyed. /usr/bin/bmccli ("bmccli v1 - A1 BMC management orchestrator CLI") exists on bmc-sa-els35-csr (hostname els100) and is the supported front end for the raw gpioset/i2cset/spi_flash_test recipes. Groups: capture (start/stop/status, FSM+SPI pcap), cpld (version/update SSPIEm), flash (version/update A1 boot NOR), device (reset, perst, power, platform-power, pwr-limit, oam-status, status), plus sled-id, version, help, search. Device-scoped verbs need -d <0-based idx|all>. Full command table + action classes recorded in references/bmc.md. Observe-only so far: nothing was executed beyond help.
- **2026-10-04 18:44Z** — UART dry run OK. BMC bmc-sa-els35-csr (192.168.177.222, hostname els100) is reachable DIRECTLY from the workstation, no jump host. Password auth only; sshpass -e with SSHPASS in env works. 12 obmc-console-servers up: ttyOAM0-7 on ports 2200-2207, SWCLI 2208/2209, SWSDB 2210/2211. No persisted console logs in /var/log, so UART must be captured LIVE. Only OAM0 and OAM1 answered a CR; OAM2-7 silent. Read+write+diag all verified on OAM1.

### srv15
- **2026-10-11 23:53Z** — CORRECTION to the previous entry - the FLR boot hang is NOT card-specific. The full n=20 run produced TWO failures, on TWO DIFFERENT cards, with TWO DIFFERENT stall signatures:
  iter 17: LAGGARD 0000:6b:00.0 state=in_progress fw_boot_sts=0x1 -> still 7/8 after 150s: HUNG
  iter 19: LAGGARD 0000:5d:00.0 state=not_started fw_boot_sts=0x0 -> still 7/8 after 150s: HUNG
Both sat stuck the full 150s (90s budget + 60s grace), so both are lockups and not timing margin. The two signatures are meaningfully different: 0x1/in_progress means the boot started and wedged mid-flow; 0x0/not_started means the FLR cleared fw_boot_sts (expected, it IS in the FLR reset domain) but the boot never kicked off at all - that is the more alarming of the two. So do not chase a single bad OAM: treat this as a general intermittent that can hit any card on the 8-OAM tray, roughly 1 stalled card-boot per 100 card-FLRs. Same 20-iteration run on single-card dsk14 was 20/20 clean, so the 8-card population matters. Also observed: iteration wall time degrades under sustained cycling (64s early -> 124s by iter 18), worth watching separately.
- **2026-10-11 23:50Z** — FLR soak n=20 (2026-10-11, PIVSY-1592 image): 16 pass / 1 fail in 19 iters on the 8-OAM tray. The one failure reproduced the earlier n=5 stall, and the BDF-naming fix caught it:
  LAGGARD 0000:6b:00.0 state=in_progress fw_boot_sts=0x1
  -> still 7/8 after 150s: HUNG
  [iter 17/20] FAIL at boot (163s)
Verdict HUNG not SLOW - the card sat stuck for the full 150s (90s budget + 60s grace), so this is a lockup and raising the timeout would not have helped. fw_boot_sts=0x1 means the card STARTED booting and never finished, i.e. the FLR was delivered and the reset took; the hang is downstream in the boot flow, not a missed FLR. Same run on dsk14 (1 card) was 20/20 clean, so the hang appears to need the 8-card population. Rate ~1 stalled card-boot per 100 card-FLRs, transient and self-clearing. OPEN: whether 0000:6b:00.0 is always the guilty card - the n=5 failure predates the BDF fix so it is unattributed. Next measurement is n>=50 tallying laggard BDFs before blaming silicon vs FW.
- **2026-10-11 23:28Z** — PIVSY-1592 FLR validation. Flashed spi_flash_image_arm_asic.bin (sha f5177772...), byte-verified, POR by user; cold boot confirmed by FLA ROM set_bits 0x52(warm)->0x4d(full load from flash) = new image live. All 8 OAMs 1d0f:7596 @64.0 GT/s x8.
FLR soak via ~/lab_tools/flr_test.sh: n=1 8/8 PASS (64s); n=5 4/5 PASS - iter2 had ONE card still state:in_progress at the 90s budget, self-cleared by iter3 and all 8 read 0x2/done after. ~1 stalled card-boot in 40, transient.
Proved an FLR does NOT reload from flash (FW_LOAD_DONE is POR-domain, survives; ROM re-inits from memory) - so the soak is the WARM path only and a new image needs a POR.

### dsk14
- **2026-10-11 23:28Z** — PIVSY-1592 FLR validation, 1 card @0000:02:00.0. POR by user; recovery needed THREE distinct fixes: (1) PCI vendor ID flipped 208b -> 1d0f:7596, (2) the octl debug container did not survive the power cycle AND a stale stopped container still held the name (docker rm, then recreate), (3) PCI COMMAND=0000 - memory decode AND bus master both off, BARs assigned but [disabled], every fw_ctl op failed until a plain setpci COMMAND write restored it.
Cold boot then confirmed via FLA ROM set_bits 0x4d (full load from flash) and last_reset_type 0x20 HOT - expected, the host cycles the link after a POR.
FLR soak n=1 1/1 PASS (45s). Harness auto-discovers vendor (tries 1d0f then 208b), which is why the vendor flip did not break it.
