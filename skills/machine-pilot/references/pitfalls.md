# Pitfalls — things that already bit us on lab stations

Accumulating list. One entry per pitfall: **symptom → cause → fix/rule**. Grouped by
theme; newest within a theme at the bottom. When a pitfall recurs across sessions,
tighten the rule in `SKILL.md`. Date = first recorded.

---

## Access & etiquette

- **Machine shows `SYSTEM ALLOCATION NOTICE … allocated to: <user>`** → SAT allocated
  the box to someone else (dsk70, srv17 on 2026-10-04) → observe-only; ask before
  touching. `Access is not limited` is *not* permission. *(2026-10-04)*
- **`sudo: a password is required`** → lab hosts have no passwordless sudo → the pilot
  cannot `rmmod`/`modprobe`/`dmesg -C`; hand those lines to the user. *(2026-10-04)*
- **`ssh root@bmc-…` → `Permission denied (publickey,password)` / BatchMode fails** →
  BMC accepts password auth only, no key installed → the user types the password in
  their terminal; never route it through the model, never retry with guesses. *(2026-10-04)*
- **`WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!` on a BMC** (`bmc-sa-els25-csr`,
  `bmc-sa-eab39-csr`) → BMC re-imaged/updated, new host key → confirm with the user
  that the BMC was reflashed, then `ssh-keygen -R <bmc>` and reconnect. Do not
  disable host-key checking globally. *(2026-10-04)*
- **`mctl sh` prints `cd: …/projects/…/fw: No such file or directory` first** → the
  host has no mirror workspace; expected with the `: || cd "$HOME"; …` prefix → ignore
  that one line, require the rest of the payload to succeed. *(2026-10-04)*
- **`kex_exchange_identification: read: Connection reset by peer`, then `No route to
  host`** (dsk61, 2026-10-04 ~15:30Z, minutes after it answered) → the host went down /
  is rebooting (bench power-cycled by someone), not an auth problem → confirm from a
  sibling lab box (`ping`, `/dev/tcp/<host>/22`), retry once later, never loop; if
  needed the user can check the bench BMC. *(2026-10-04)*

## Device identification

- **`lspci -d 208b:` returns nothing on a tray that is clearly up** → the parts enumerate
  with the Amazon vendor ID `1d0f:7596` → always scan both IDs. *(2026-10-04)*
- **BARs show `[disabled]` on every device** → no kernel driver bound (memory decoding
  off), not a dead device → check `Kernel driver in use` / `lsmod`. *(2026-10-04)*
- **"It's a stand-alone" / "it's a riser" argued from `lspci`** → the SE loopback is not
  on the host bus; both look identical → the memoir's declared type wins; ask when
  absent. *(2026-10-04)*
- **`lspci` says `rev 01`, docs say ASIC rev `0x22`** → the `lspci` revision is not the
  variant-identifying DBI revision `fw-flasher` reads → don't use it to argue variants.
  *(2026-10-04)*
- **Device vanished from `lspci` after a reset/experiment** → link dropped; the saved
  `fw_ctl` inventory (`/var/log/atl/fw_ctl/pcie_inventory.json`, `pcie devices`) still
  knows its parent/root port for `por_safe_reset`. *(2026-10-04)*
- **Tray host shows `k < 8` devices** → missing OAMs are a finding (power, link,
  training), not noise → report `k/8` with BDFs; consult the tray BMC before blaming FW.
  *(2026-10-04)*
- **`lspci -vv` shows no `LnkSta`/`LnkCap`** → non-root users get `Capabilities:
  <access denied>` → read `/sys/bus/pci/devices/0000:<BDF>/current_link_speed` and
  `current_link_width` (world-readable) — the probe already does. *(2026-10-04)*
- **A device is enumerated but its link reads `Unknown x63` or `2.5 GT/s`** (srv17
  `6e:00.0`, `66:00.0` on 2026-10-04) → link status garbage / stuck at Gen1 = not
  really trained; a `32 GT/s` reading on a Gen6 part = downgraded → per-OAM finding,
  record BDF + reading in the memoir and hand to `bug-triage`. *(2026-10-04)*
- **Tray BDFs change across reboots** (srv17: `5d` vanished, `68`/`6b` appeared after a reboot) and **one tray mixes `208b` and `1d0f` modules** → never key the OAM↔BDF map on remembered BDFs; re-derive per boot (`pilot devices`), and never assume a tray has a single vendor ID. *(2026-10-04)*
- CORRECTION to the "1d0f = FW did not reach vendor-ID programming" heuristic: that no longer holds on current A1 FW (0.5.15, a1_power_on_branch, Oct 2026). Observed 2026-10-11 on srv15: ALL 8 OAMs enumerate as 1d0f:7596 while simultaneously reporting fw_boot_sts 0x2 / state: done, link 64.0 GT/s x8, and passing a full FLR soak (arm/flr/bus/boot/BAR/KMD). dsk14 likewise flipped 208b -> 1d0f across a POR and is perfectly healthy. So 1d0f alone is NOT a boot-progress failure signal on this image - treating it as one raises a false alarm. Confirm boot with host_lib boot fw-sts (state: done) rather than inferring from the vendor ID, and set fw_ctl "pcie vendor 1d0f" accordingly. *(2026-10-11)*

## Tooling

- **`fw_ctl` errors "hostname matches no known pattern"** on `sa-dsk*`/`sa-srv*` → its
  auto-variant only knows `a1-*`, `vlsi-fpga*`, `pldm-*` → pass `--variant asic`. *(2026-10-04)*
- **`fw_ctl`: "Multiple Element Labs PCIe devices found … cannot auto-select"** → a
  tray has >1 endpoint → `pcie devices` then `--bdf <BDF>` (or `--all` / `--device a b`).
  *(2026-10-04)*
- **`source /opt/atl/fw/scripts/fw_ctl/setup_fw_ctl_env.sh` → no such file** on the
  host → `/opt/atl` is **not** installed on lab hosts; it is the integration
  workspace's `build_artifacts/opt/atl`, mounted at `/opt/atl` inside the `octl ws exec`
  container → run company tools via `pilot -t <st> exec '<star-cmd>'` from
  `~/projects/iridium/a1_power_on_branch/ws-integration-ci` (see host-ops), not from the
  bare host shell. *(2026-10-04)*
- **Company tool behaves oddly / old components** → it was run from a dev or
  single-component workspace (e.g. `one/fw` = `psoc-fw-ci`) → run it from the
  **integration-manifest** workspace (`pilot ws` marks it `*`), which pins the latest
  of *all* SW components (KMD `drv-ci`, `psoc-fw-ci`, `regcli-ci`, `simulator-ci`, …).
  *(2026-10-04)*
- **`/var/log/atl/fw_ctl/pcie_inventory.json: Permission denied`** (srv17) → fw_ctl
  is run as root on tray hosts, file is `root:root 600` → you cannot read the saved
  inventory; ask the user to paste `pcie devices`. *(2026-10-04)*
- **`pgrep -af tio` (or any short name) floods the holders list** with `migration/N`,
  `notifications`, `actions-runner` → substring match → anchor patterns
  (`(^|/| )(minicom|…|tio)( |$)`); the probe does. *(2026-10-04)*
- **Token burn: pasting a full `dmesg`/`lspci -vv`/`ls -la` into chat** → always go
  through `pilot` (bounded, pre-digested) or `| tail -n 40`; save the full thing with
  `pilot … evidence` / `dmesg --save` and quote the lines that matter. *(2026-10-04)*
- **`mctl sh` output looks truncated/garbled for binaries** → `mctl sh` captures text
  via command substitution (NUL bytes lost) → never stream images/tar through it; use
  `mctl pull-build` or the base64 pattern in `fw-deploy`. *(2026-10-04)*
- **Two BMC names for one tray host** (`bmc-sa-srv17-csr` *and* `bmc-sa-els17-csr`) →
  the `srv` one is the server's BMC; OAM UARTs/`oem_show_version` live on the **`els`**
  BMC → check `hostname` after login. *(2026-10-04)*
- bmccli flash version reports build=<manifest version> which is IDENTICAL across images built from one manifest - it is NOT a flash discriminator. The fpt_uuid field IS image-derived and unique per image (found at 0x40014/0x41014/0x42014 = FPT A/B/C). Verify a flash by extracting the uuid from the .bin offline and asserting bmccli flash version -d all reports it on all devices/FPTs. *(2026-10-04)*
- octl ws exec --args parsing (fw_parameter_args_get in fw_build.star) is a naive args_str.split(" ") with NO quote handling: cmd="bmc load /tmp/x.bin --boot" silently truncates to cmd=bmc and the rest is DISCARDED - a multi-word verb appears to run but does something else. Pass only space-free values. For a multi-word fw_ctl verb use a script file: write /tmp/f.cli then --args "variant=asic skin=cli cmd=--script=/tmp/f.cli", or use skin=shell for an interactive container shell. *(2026-10-04)*
- octl ws exec takes an ARGV, not a shell string: quoting the whole star-cmd (shlex.quote on the full line) makes octl read the entire line as the target name and fail. Tokenize and quote each word separately. Fixed in pilot cmd_exec 2026-10-04. *(2026-10-04)*
- pilot exec auto-detected ws comes back as ~/projects/... from list_ws; shlex.quote made it a literal tilde the remote shell will not expand, so cd failed. Use "$HOME"-prefixed quoting for ~ paths (remote_path helper). *(2026-10-04)*
- run_fw_ctl star: fw_parameter_args_get does a naive args_str.split(" ") with NO quote handling, so a multi-word cmd= value (the docstring example cmd="reg read NAME" is wrong) silently truncates to its first word. Drive multi-word verbs with a replay file instead: write one verb per line to /tmp/x.cli and pass cmd=--script=/tmp/x.cli (argparse accepts --script=VALUE, no space; /tmp is mounted into the container). *(2026-10-04)*
- run_fw_ctl without an explicit variant= fails with: reading starfile ... key "vendor" not in dict. Cause: the no-variant path calls _get_variant_from_hostname() which reads project_env["vendor"], absent in this env. Always pass variant=asic. *(2026-10-04)*
- PEX90104 SWCLI console (BMC ports 2208/2209) command discovery: "help", "?", "rev", "ver", "version", "list", "commands", "man", "showport", "linkstat" are ALL rejected with "Invalid command. Use help for CLI command reference" - including help itself, so the error message is a dead end. The command that actually works is pcieportprop: bare "pcieportprop" prints the FULL port table (per-port link state, Rx Detect, negotiated/max speed+width, DS bus ranges, phys slot, device count); "pcieportprop host" prints only the host-port bitmask. Console often ignores the FIRST command after connect - send a bare \r to prime, then the real command, and allow ~8s to read the full table. *(2026-10-04)*
- TWO DIFFERENT PCIe switch interfaces - do not confuse them. (A) SWCLI = plain TEXT console, BMC ssh ports 2208/2209, prompt "cmd>", tiny command set (pcieportprop works; help/rev/ver do not). (B) SDB = FRAMED BINARY interface, BMC ports 2210/2211 or host /dev/ttyUSB* via the ELS front-panel USB cable, driven by the switchclid binary (on srv17 at /usr/local/bin/switchclid, also /usr/local/bin/switchcli). SDB is the powerful one: switchclid -sdb /dev/ttyUSB<N> <subcmd>, or -pci <bdf> once the host driver and links are up. NOTE on srv17 there is currently NO /dev/ttyUSB* (front-panel USB cable not attached), so the SDB route is unavailable there and only the SWCLI text consoles work. *(2026-10-04)*
- switchclid (SDB) subcommand reference for Broadcom PEX90104, from Confluence via ROVO. STATUS: swstatus -sbr = firmware status, SBR version, migration state. FLASH: erase -f = full SPI flash erase (prompts yes); dl -f <managed_mode.fw> = flash full managed-mode FW; dl -p <img> -o <offset> = program a partition at a byte offset (ILFW.signed -o 0x40000; MiniSBR_with_FT.sbr.bin -o 0; composite.bin -o 0x0). SBR: sbr -d -b <miniSBR.bin> -type mini; sbr -d -r 0 -type main -b <mainSBR_signed.imd.signed>. IMAGE BUILD/SIGNING (host side): xmltobin -s <schema.xsd> -x <in.xml> -o <out.bin>; hash -sha3 512 -b <file.bin> -o <file.unsigned>; insert -p <pubkey> -f <in_fw> -o <out_fw> -s <signed_hash>; verify -f <signed> -kv <pubkey>; composite -concat -img1 .. -img1offset .. -img2 .. -img2offset .. -size .. -o out.bin; composite -metadata. CAUTION: erase/dl/sbr are DESTRUCTIVE and need a COLD power cycle afterwards to reload SBR tables and exit recovery mode. Unmanaged mode = direct register/AXI access, static link status. Managed mode = dynamic port health, live link-training state, error counters, eye-diagram capture. *(2026-10-04)*
- This workstation has NO ping and NO nc. A poll loop using `nc -z host port 2>/dev/null` silently fails every iteration (command-not-found is swallowed) and reports "waiting" forever regardless of real host state -- a 7.5min wait proved nothing. Use `timeout 5 bash -c "</dev/tcp/HOST/PORT"` instead, and verify the probe tool exists before trusting any wait loop. *(2026-10-06)*
- A single `ssh: connect to host X port 22: Connection timed out` does NOT prove a host is down -- it is often a reboot window or transient blip. 2026-10-06 dsk61 gave that error, then answered normally ~40s later reporting `uptime -p` = "up 0 minutes". Confirm with 2-3 spaced /dev/tcp probes plus a known-good control host before declaring a station dead, and check `uptime -p` on reconnect: "up 0 minutes" means you landed mid-reboot and the owner is probably still mid-workflow. *(2026-10-06)*
- FLR toolkit saved 2026-10-11 to the SHARED NFS home at ~/lab_tools/ - visible from EVERY lab station, no per-host copy needed:
  flr_test.sh      FLR soak harness. usage: flr_test.sh [-n ITER] [-d "bdf ..."] [-v VENDOR] [-c CONTAINER] [-w WS] [-t BOOT_TIMEOUT] [-g BOOT_GRACE]. Per iteration per card: arm witness (clear fw-sts + seed scratch) -> FLR via sysfs -> bus check -> wait boot done -> verify last_reset_type==FLR -> BAR write/verify/restore -> KMD load -> KMD remove. Auto-discovers devices (tries vendor 1d0f then 208b), so it survives the 208b/1d0f identity flip. Prints __FLR_TEST_DONE__ iters=N pass=P fail=F devices=D as a machine-readable sentinel. On a boot timeout it names the laggard (LAGGARD <bdf> state=.. fw_boot_sts=..) and then keeps polling for BOOT_GRACE seconds to separate a SLOW boot from a HUNG one.
  flr_probe.sh     decodes RST_TYPE_0..7_RST_TYPE_CFG (bit8 RE_BOOT, bit0 MSK) and samples boot state at t+0/1/2/4/8/14 s across an FLR.
  flr_rom_probe.sh issues the FLR from INSIDE the fw_ctl script and burst-samples ROM progress - the only reliable way to catch the ~2 s transient.
  fwctl.sh         quoting-safe one-shot fw_ctl runner.
ALWAYS launch detached with a per-host log: setsid nohup ~/lab_tools/flr_test.sh -n 20 > ~/flr_n20_$(hostname).log 2>&1 < /dev/null &
Do NOT overwrite a script while a run is executing it - bash reads scripts incrementally and will corrupt mid-run. *(2026-10-11)*

## UART

- **`/dev/ttyACM0` capture is 0 bytes** → silence, wrong node, wrong baud, or another
  reader → `ls -l /dev/serial/by-id/` (nuc74: `ttyACM0`=MCP1, `ttyACM1`=MCP2),
  `stty -F … -a | head -1` (a fresh node sits at **9600**; set 115200 before a raw
  `cat`), `pgrep -a minicom`. *(2026-10-04)*
- **`minicom: Device /dev/ttyACM0 is locked` / someone's console hangs** → a second
  reader opened the port (minicom left running in tmux is the classic) → find and ask
  the owner; never kill it unasked; never delete `LCK..` files for a live process.
  *(2026-10-04)*
- **`~/minicom.log` offered as "the UART log"** → it is minicom's connection/hangup log
  (`YYYYMMDD HH:MM:SS Hangup (…)`), not console output → real captures need `-C <file>`
  or `uart_capture.sh`. *(2026-10-04)*
- **Boot banner missing from the capture** → capture started after the reset → start the
  bounded capture *before* triggering reset/flash and let it run across the event.
  *(2026-10-04)*
- **`tail -f` on the BMC `/var/log` UART file shows nothing while a console is open** →
  by design (Confluence): collection continues in the background; read it after
  closing the live console. *(2026-10-04)*
- **Port numbers 2200–2207 are OAM0–7; 2208–2209 are the PCIe *switch* UARTs; 2210–2211
  PCIe SDB** → connecting to 2208 and expecting an A1 banner is a wrong-port, not a
  dead OAM. *(2026-10-04)*
- **OAM console input: do not try to drive it FROM the BMC** — four dead ends, all
  confirmed on els100: (1) `nc 127.0.0.1 220<n>` returns an **SSH key-exchange banner**
  — those ports are dropbear, not raw console; (2) `obmc-console-client -i ttyOAM<N>`
  on a pipe prints nothing (needs a controlling terminal) and *still* does not forward
  stdin once given one — the server log shows the command never reached the device;
  (3) BusyBox has no `timeout` and no `script`, and its Python ships without `pty`/
  `termios` (`os.openpty()` does exist); (4) writing straight to `/dev/ttyOAM<N>`
  produces no echo. **The only working path is from the workstation:**
  `ssh -tt -p 220<n> root@<bmc>` — i.e. `oam_exec`, wrapped in `job` for detachment.
  *(2026-10-05)*
- **A console returning ~1 byte is a wedged shell, not a transport bug** — but first
  rule out a stale holder (one client per console). If the port is free and the BMC's
  own `/var/log/obmc-console-ttyOAM<N>.log` **mtime is stale vs `date`**, the UART is
  genuinely silent → reset that device by *explicit index*. *(2026-10-05)*

## Evidence & NFS

- **`rsync -a` to the shared home fails with `chgrp … Operation not permitted` (exit 23)**
  → NFS squashes group changes → `--no-owner --no-group` (fw-deploy rule); verify bytes
  by hash, not by rsync's status. *(2026-10-04)*
- **`ls /validation/...` hangs** → `soft` NFS mount with a slow/absent server → it is a
  storage issue; don't conclude permission denied; retry later / use `timeout 20 ls`.
  *(2026-10-04)*
- **"The image on NFS is newer because its branch advanced"** → provenance is the hash
  + `CHANGE_NOTES.md`, never the branch tip → identify images by `sha256sum` and the
  notes. *(2026-10-04)*
- THE LAB HOMES ARE ONE SHARED NFS VOLUME - every station sees the same /users/home/<user>. Proven 2026-10-11: stat -c %i ~/flr_n5.log returned the IDENTICAL inode (53163505) on sa-srv15-csr and sa-dsk14-csr, stat -f -c %T $HOME = nfs on both. Consequence: two stations each running `cmd > ~/run.log` CLOBBER one another and you silently analyse one hosts results believing they are the others. The tell in our case was a 1-card host reporting devices=8. ALWAYS redirect per host: > ~/run_$(hostname).log. Upside: staging a helper script once makes it visible from every station (no per-host copy needed - still md5sum to confirm). Same caveat applies to the integration workspace ~/projects/.../ws-integration-ci, which also lives on that shared home, so concurrent octl ws exec runs from two stations share one workspace. *(2026-10-11)*

## BMC shell
- bmc-sa-els35-csr runs BusyBox with NO timeout applet and head needs -n. Bounding a console capture on the BMC silently fails with rc=127 and looks like a DEAD/SILENT console. Bound captures from the LOCAL side instead: pipe into local timeout around sshpass ssh -tt -p 2200+N root@BMC. *(2026-10-04)*

## Local shell
- The login shell aliases wc to wip_checkout, so any $(wc -c < file) in a pilot one-liner prints wip_checkout: command not found and yields an empty count. Use /usr/bin/wc explicitly in evidence scripts. *(2026-10-04)*

## Lab measurement
- LTSSM logger: ALWAYS `dwc_ep_ctl ltssm_logger_dump` BEFORE `ltssm_logger_clear`. On the ELS tray the link fell from L0 back to Detect.Quiet within minutes, and clearing first destroyed the transition history of the exact event under investigation. Order every sampling cycle as dump-then-clear, never clear-then-wait. *(2026-10-04)*
- Host PCI hot-rescan CANNOT recover our device: a rescan enumerates it but every BAR fails with "cannot assign; no space" (BAR2 alone is 0x4000000000 = 256GB). Bridge windows are sized at BOOT, and if the EP link was down at host boot the windows are too small to ever fit the BARs later. Consequence: lspci showing the device after a rescan does NOT mean it is usable - fw_ctl will still read 0xFFFFFFFF. Correct order for any PCIe test: bring ALL device links up FIRST, then reboot the host so enumeration+BAR allocation happen normally. *(2026-10-04)*
- Our device enumerates under TWO PCI vendor IDs. FW programs vendor_id=0x208b device_id=0x0001 early in boot (dwc_ep_ctl_set_vendor_device_id_impl), but the pre-FW/default identity is 1d0f (Amazon/Annapurna, e.g. 1d0f:7596, class 1200 Processing accelerators). ALWAYS check both: lspci -d 208b: AND lspci -d 1d0f:. Checking only 208b reports a false "0 devices". A device visible as 1d0f means it trained its link but FW did not reach the vendor-ID programming step - a useful FW-progress signal in itself. *(2026-10-04)*
- LTSSM state Disabled (0x19) on our EP is PARTNER-DIRECTED, never self-inflicted. Signature seen on els35 oam0+oam4 (byte-identical): Detect.Active -> Polling.Active -> Polling.Configuration -> Configuration.Linkwidth.Start -> Disabled.Entry (reason 0x43) -> Disabled.Idle -> Disabled. The EP trained normally until the upstream switch port drove it into Disabled via TS1 with the Disable-Link bit. Cross-check at the switch with pcieportprop: the matching DS port shows Link=Down with Rx Detect=Present (device is there and terminating, link refused). Polling.Compliance (0x03) is the sibling symptom - partner detected electrically but never returned valid TS ordered sets. For BOTH of these, look upstream at the switch/host before suspecting our FW. *(2026-10-04)*
- dwc_ep_ctl link_status reporting "L0" can HIDE severe link instability - always follow it with ltssm_logger_dump. On els35 oam6 link_status said L0 Gen6 x8 while the LTSSM logger was FULL (256 entries, wrapped) with one cycle repeating endlessly: Recovery.RcvrLock (reason 0x11) -> Recovery.RcvrCfg -> Recovery.Idle -> L0. oam2 showed the same churn plus repeated FULL re-equalization (Recovery.Equalization.Phase0..Phase3) and Recovery.Speed, which is what produced its Gen6->Gen5 downgrade. A logger that is "full" is itself the alarm: it means hundreds of transitions since boot. Logger state is in the dump header (entries count, Enabled, Halt on full, Control, Start slot). *(2026-10-04)*
- Two traps that each produced a CONFIDENTLY WRONG answer on 2026-10-11.
(1) pgrep -fc <script> COUNTS ITS OWN SHELL: the ssh/bash -lc wrapper carrying the pattern in its argv matches, so it returns >=1 even when nothing is running. We reported a soak as "still running" when it had finished. Match a longer-lived specific string (pgrep -f "flr_test.sh -n 20") and corroborate against the log tail or a DONE sentinel the script prints.
(2) ONE fw_ctl invocation costs ~1.5 s, which is most of the ~2 s post-FLR boot window - so polling with separate per-sample calls misses the transient entirely and looks like "the register never changed". To observe anything across a reset, issue the reset from INSIDE the fw_ctl script (sh bash /tmp/doflr.sh) and burst-repeat the read lines in the same session, then uniq -c. That is how we proved fw_boot_sts IS cleared by an FLR (0x2 -> 0x0 -> 0x2), reversing an earlier wrong conclusion. *(2026-10-11)*

## Lab hardware

- **`bmccli device reset -d all` silently skips non-READY devices** ("reset requested
  for all *ready* devices") → the devices you actually need to recover are usually
  `INIT_FAILED`, so `-d all` does nothing for them → reset by **explicit index**
  (`-d 3`). Verified: `-d 0` revived a console `-d all` had ignored. *(2026-10-05)*
- **`state=INIT_FAILED` + `eid=0` is BMC-side MCTP/PLDM init, NOT device boot** →
  devices sit in `INIT_FAILED` with perfectly working shells. Use `device oam-status`
  for real hardware state; **`boot=3` = FW booted**. Do not read `INIT_FAILED` as
  "device dead". *(2026-10-05)*
- OAM with NO UART echo at all (dead FW shell) while BMC reports present=1 pwrgd=1 and boot=2: this is the known D2D sideband-interface init hang - sideband init gets stuck and freezes the boot. It happens intermittently and is NOT a PCIe/link problem. Known issue, fix deferred. Remedy for now: reset the affected device (bmccli device reset -d N from the BMC, out-of-band, no PCIe needed); it comes back with a live shell. Seen 2026-10-05 on els35 oam1+oam7, both fully recovered by a reset and trained to L0 Gen6 x8. *(2026-10-04)*

## Allocation
- probe `alloc=free` is NOT proof a bench is idle. 2026-10-06: dsk61 reported alloc=free while sivaprakash.m was logged in (pts/0) running `octl ws debug --privileged` and had rebooted the host 9min earlier. Conversely dsk70 showed `holders=none` while dmesg proved someone had been FLR-cycling the device all morning. Always cross-check `who` + `holders=` + recent dmesg before claiming a station. *(2026-10-06)*

## PCIe
- fw_ctl "pcie transport bring-up failed: BAR read 0xFFFFFFFF (completion timeout / BAR not routed)" => check RAW config space, not lspci text. `lspci -vvv` prints "Region 0: Memory at 4800000000 [size=256M]" which is the kernel INTENDED resource; `lspci -xxx` offset 0x10 showed `0c 00 00 00` = flags only, base address ZERO, i.e. the BAR was never programmed into the device. Root cause was a failed FLR ("not ready 1023..65535ms after FLR; giving up" + "Data Link Layer Link Active not set"). Device enumerates and shows mem=on/link 64.0G x8 yet is unreachable. PCIe flashing is impossible in this state -- recover with host reboot (re-asserts PERST + reassigns BARs) or PCIe remove+rescan. *(2026-10-06)*
- After a POR (and also after a PCI rescan) the device can come back with CONFIG COMMAND=0000 - memory decode AND bus master both disabled. lspci shows the BARs assigned but marked [disabled], every BAR read returns all-ones, and every fw_ctl op fails in a way that looks like dead firmware. It is NOT a FW fault. Fix as root in the privileged container: setpci -s <bdf> COMMAND=0x0006 (mem+busmaster), or 0x0002 for mem alone. Seen 2026-10-11 on dsk14 after a POR and earlier on srv15 after a rescan. NOTE: a masked write like COMMAND=0x0002|0x0002 can be rejected for syntax; a plain write is safe here precisely because the register reads 0000, so there are no bits worth preserving - but READ it first and only plain-write when it is genuinely 0. *(2026-10-11)*

## SPI
- Detect whether the FTDI SPI channel is FREE before trying to flash via nuc/ftdi-spi. `fw_ctl ftdi list` reporting "(no access -- in use or insufficient permissions)" is AMBIGUOUS -- it says the same thing for a benign kernel binding and for a real conflict. Disambiguate from sysfs: `[ -L /sys/bus/usb/devices/<usbpath>:1.<n>/driver ]`. BOUND->ftdi_sio = benign (just a serial port, nobody using it for SPI); NO driver symlink = that interface was detached for libusb/MPSSE, i.e. it IS the SPI channel and someone holds it. Cross-check with the /dev/ttyUSB* count: it drops by one when a channel is detached for MPSSE. 2026-10-06 nuc74: 1-8.2:1.1 had no driver (ttyUSB count 10->9) and was held by another user`s 3h fw_ctl console. Also check `w`/`who -u` idle times, NOT just process CPU time -- a console with 7s CPU over 3h looks dead but its owner may have logged in minutes ago. *(2026-10-06)*

## octl on lab hosts
- octl ws init fails with "git@github.com: Permission denied (publickey)" + "octl: command not found" -> pilot run uses ssh WITHOUT -A and a non-login shell. Fix: run the git-touching step (ws init) in the foreground over `ssh -A -o ControlPath=none <host>` with `export PATH=$HOME/.local/bin:$PATH`; only the S3-only step (octl artifacts download) can be detached via pilot run --detach. *(2026-10-09)*
- An octl ws debug --privileged --detached container does NOT survive a station power cycle, and worse, a STALE STOPPED container keeps holding the name - so recreating it fails with a name conflict while docker ps (running only) shows nothing. After any POR: docker ps -a | grep -- -dbg, docker rm <name>, then recreate. Seen 2026-10-11 on dsk14.
Also: the fw_ctl container does NOT mount the NFS home, so any image or script you want it to read must be staged into /tmp first - reading straight from /users/home/... fails instantly with a -2 (ENOENT) that can masquerade as a flash/verify error.
Finally, a detached soak launched with setsid nohup survives fine, but redirect its log PER HOST (see Evidence & NFS) or two stations overwrite one file. *(2026-10-11)*
