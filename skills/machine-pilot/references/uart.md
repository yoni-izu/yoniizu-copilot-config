# UART — per setup type (source: Confluence RPI "View UART On ELS Systems", 2026-10-04)

Rules: one reader per port · reading is observe, typing is touch · captures are bounded
and saved to evidence · know which adapter backs the node (`uart ports` shows by-id + baud).

## `pilot` verbs

| Verb | Does |
|---|---|
| `uart ports` | nodes + by-id + current baud + perms, lock files, holders (bench host / NUC) |
| `uart capture [PORT] [SECS] [--topic T]` | ships [uart_capture.sh](../scripts/uart_capture.sh): refuses a held port, `stty 115200 raw`, `timeout SECS cat` → `evidence/<st>/<ts>_T/uart_<port>.log`; honours registry `uart=<nuc>:/dev/ttyACMn` (captures on the NUC) |
| `uart console [OAM]` | prints the human command (interactive — you cannot sit in it) |
| `uart bmc-ls` | tray BMC: `ls -la /var/log` filtered + `/etc/obmc-console/*.conf` (password → user) |
| `uart bmc-pull --file F \| --oam N [--topic T]` | `scp` BMC `/var/log/<file>` into evidence; `--oam` is a name heuristic — list first |

## ELS tray (tray BMC `bmc-sa-els<NN>-csr`, login `root`, password auth)

| UART | BMC port | On the BMC |
|---|---|---|
| OAM0…7 | `2200…2207` | `/usr/bin/obmc-console-client -c /etc/obmc-console/client.220N.conf` |
| PCIe switch | `2208`, `2209` | `client.2208/2209.conf` |
| PCIe SDB | `2210`, `2211` | `client.2210/2211.conf` |

- Live: `ssh -tt -p 2200+N root@<bmc>` (escape `~.`). Port 2208 showing no A1 banner is a
  *wrong port*, not a dead OAM.
- Persisted: every OAM/switch UART is written continuously to a file in BMC `/var/log/`
  (names: discover with `uart bmc-ls`, not guessed). **Do not `tail -f` it while a live
  console to that OAM is open** — read it afterwards. BMC storage is small; pull promptly.
- OAM index ↔ host BDF is per tray **and per boot** — derive from the console banner /
  `fw_ctl`, record in the memoir with the boot time.

## Stand-alone / riser (USB serial adapter on the host or a supplementary NUC)

- Expected node **`/dev/ttyACM0`**, **115200 8N1** (`/etc/minicom/minirc.dfl`), no flow
  control; node is `crw-rw-rw- root dialout`, user is in `dialout` → no sudo.
- Which box has it: registry `uart=` (`/dev/ttyACM0` = on the host; `nuc74:/dev/ttyACM0` =
  on that NUC). dsk61/dsk70 have **no** local tty; nuc74 has `ttyACM0`=MCP2221 "MCP1",
  `ttyACM1`="MCP2", FTDI quad `ttyUSB0–3`, FT4232H `ttyUSB4–7`, FT2232 dual `ttyUSB8–9`.
- Live: `ssh -t <box> minicom -D /dev/ttyACM0` (exit `Ctrl-A X`); add `-C file` to log.
  A minicom left in tmux keeps the port busy for everyone.
- Capture: `pilot -t <st> uart capture /dev/ttyACM0 60` — start it **before** the
  reset/flash/test. `0 bytes` = silence, wrong node, wrong baud (fresh node sits at 9600
  until configured) or another reader; check `uart ports`.
- Input (touch): `printf 'help\r' > /dev/ttyACM0` while a capture runs — prefer `fw_ctl`
  / `fw_host_cli` over typing into the console.

## Decision

```
registry uart starts with "bmc:"  → BMC path (live: ssh -p 2200+N; evidence: bmc-ls/bmc-pull)
registry uart is /dev/… or <nuc>:/dev/… → serial path (live: minicom; evidence: uart capture)
registry uart is "?"               → ask / probe host then NUC → `pilot stations set <st> uart=…`
```
