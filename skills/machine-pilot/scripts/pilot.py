#!/usr/bin/env python3
"""pilot — token-cheap lab-station operator (machine-pilot skill). Local CLI; talks ssh.

usage: pilot [-t STATION|mctl-target|user@host] [-v] [--json] CMD [ARGS]

  stations [set STATION k=v ...]    registry (references/stations.tsv): host type bmc uart notes
  probe                             one-screen summary: alloc, devices+link, setup guess, serial, nfs, bmc, dmesg
  devices                           our devices only (lspci -d 208b: + 1d0f:, via sysfs)
  dmesg [--grep RE] [--tail N] [--since 'HH:MM'|'Mon D HH:MM'] [--save TOPIC]
  uart ports | capture [PORT] [SECS] [--topic T] | console [OAM] | bmc-ls | bmc-pull (--file F | --oam N) [--topic T]
  bmc versions | fru [BMC_DC_SCM|EA100_UBB]      (observe-class BMC verbs; password typed by the user)
  run 'CMD' [--tail N] [--detach TAG]   status TAG
  ws                                octl workspaces on the host (manifest, version, revision; * = integration)
  exec 'STAR-CMD' [--ws PATH] [--tail N]   cd WS && octl ws exec 'STAR-CMD'; WS defaults to the integration ws (touch-class)
  nfs deployments [N] | notes DIR | hash PATH | ls PATH
  evidence TOPIC [--bmc] [--oam N] [--tty PORT] [--secs S]
  session open TOPIC | session close FILE
  memoir 'TEXT'                     dated entry under ### STATION in references/memoir.md
  pitfall THEME 'TEXT'              dated entry under ## THEME in references/pitfalls.md

Observe-only by design: no reset/power/flash/mux verbs exist here (see references/bmc.md, fw-flasher).
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
REF, SCRIPTS, SESSIONS = SKILL / "references", SKILL / "scripts", SKILL / "sessions"
STATIONS, MEMOIR, PITFALLS = REF / "stations.tsv", REF / "memoir.md", REF / "pitfalls.md"
PILOT_DIR = Path.home() / ".pilot"
EVIDENCE = PILOT_DIR / "evidence"
MCTL_TARGETS = Path.home() / ".mctl" / "targets"
BANNER = re.compile(r"SYSTEM ALLOCATION NOTICE|allocated via SAT|Access is not limited|END OF NOTICE|^-+$|^\s*$")
DMESG_SIG = r"atl |el_pci|el_ctl|el_accel|208b|1d0f|7596|FW channel|PSOC|fw_boot|SEI|split read|poison"
VERBOSE = False


def utc(fmt="%Y-%m-%dT%H:%M:%SZ"):
    return datetime.now(timezone.utc).strftime(fmt)


def die(msg, rc=2):
    print("pilot: " + msg, file=sys.stderr)
    sys.exit(rc)


# ---------------------------------------------------------------- registry
def load_stations():
    rows = {}
    if STATIONS.exists():
        for line in STATIONS.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            f = (line.split("\t") + [""] * 7)[:7]
            rows[f[0]] = dict(zip(["station", "host", "type", "bmc", "uart", "notes", "verified"], f))
    return rows


def save_stations(rows):
    head = [l for l in STATIONS.read_text().splitlines() if l.startswith("#")] if STATIONS.exists() else []
    body = ["\t".join(r[k] for k in ["station", "host", "type", "bmc", "uart", "notes", "verified"]) for r in rows.values()]
    STATIONS.write_text("\n".join(head + body) + "\n")


def mctl_target(name):
    if MCTL_TARGETS.exists():
        for line in MCTL_TARGETS.read_text().splitlines():
            f = line.split()
            if len(f) >= 4 and f[0] == name and f[1] == "remote":
                return "%s@%s" % (f[2], f[3])
    return None


def resolve(target):
    """-> (station_key, user@host, station_row|None)"""
    if not target:
        die("need -t STATION|mctl-target|user@host")
    rows = load_stations()
    if target in rows and "@" in rows[target]["host"]:
        return target, rows[target]["host"], rows[target]
    t = mctl_target(target)
    if t:
        return target, t, rows.get(target)
    if "@" in target:
        return target.split("@", 1)[1].split(".")[0].replace("sa-", "").replace("-csr", ""), target, None
    if target in rows:
        die("station %s has no host (host=%s); set it: pilot stations set %s host=user@host" % (target, rows[target]["host"], target))
    die("unknown target %s (not in stations.tsv, not an mctl remote target, not user@host)" % target)


# ---------------------------------------------------------------- ssh
def ssh(host, cmd, stdin=None, batch=True, timeout=180, login=False):
    """Run cmd on host; returns (rc, stdout, stderr-without-banner)."""
    PILOT_DIR.mkdir(exist_ok=True)
    opts = ["ssh", "-o", "ControlMaster=auto", "-o", "ControlPath=%s/cm-%%C" % PILOT_DIR, "-o", "ControlPersist=300",
            "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=3", "-o", "LogLevel=ERROR"]
    if batch:
        opts += ["-o", "BatchMode=yes"]
    remote = ("bash -lc " if login else "bash -c ") + shlex.quote(cmd) if cmd else ""
    try:
        p = subprocess.run(opts + [host] + ([remote] if remote else []), input=stdin, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        return 124, b"", b"timeout after %ds" % timeout
    err = b"\n".join(l for l in p.stderr.splitlines() if VERBOSE or not BANNER.search(l.decode("utf8", "replace")))
    return p.returncode, p.stdout, err


def ssh_text(host, cmd, **kw):
    rc, out, err = ssh(host, cmd, **kw)
    return rc, out.decode("utf8", "replace"), err.decode("utf8", "replace")


def ship(host, script, args="", interpreter="bash -s --", **kw):
    """Send a local script over stdin: ssh host '<interpreter> args' < script."""
    return ssh(host, "%s %s" % (interpreter, args), stdin=(SCRIPTS / script).read_bytes(), **kw)


# ---------------------------------------------------------------- probe / devices
def remote_probe(host, only="all"):
    rc, out, err = ship(host, "probe_remote.py", only, interpreter="python3 -")
    if rc != 0 or not out.strip():
        die("probe failed rc=%d: %s" % (rc, err.decode("utf8", "replace").strip()[-400:]))
    j = json.loads(out)
    if "allocated via SAT" in err.decode("utf8", "replace") and not j.get("alloc"):
        j["alloc"] = j.get("alloc") or "SAT(unknown user)"
    return j


def dev_line(d):
    flag = "" if (d["speed"] == d["max_speed"] and d["width"] == d["max_width"] and "Unknown" not in d["speed"]) else "  !"
    return "  %-8s %s %s x%s  drv=%s mem=%s bar0=%s bar2=%s%s" % (
        d["bdf"], d["vid"], d["speed"].replace(" PCIe", "").replace(" GT/s", "G"), d["width"], d["driver"] or "-",
        {True: "on", False: "off", None: "?"}[d["mem_en"]], d["bar0"], d["bar2"], flag)


def short_id(i):
    return re.sub(r"^usb-|_Technology_Inc\.|-if0\d(-port0)?$|_RS232-HS", "", i) if i else "?"


def render_probe(j):
    devs = j["devices"]
    vids = ",".join(sorted({d["vid"] for d in devs})) or "-"
    lines = ["%s  %s  up %s  user=%s  alloc=%s  sudo=%s" % (
        j["host"], j.get("kernel", ""), j.get("uptime", "?"), j.get("user", "?"), j.get("alloc") or "free",
        "yes" if j.get("sudo") else "pw"),
        "setup: %s  (vid %s, switch=%s)" % (j["guess"], vids, "yes" if j["switch"] else "no")]
    lines += [dev_line(d) for d in devs]
    acm = ["%s(%s)" % (n["dev"].replace("/dev/", ""), short_id(n["id"])) for n in j["serial"] if "ACM" in n["dev"]]
    usb = sum(1 for n in j["serial"] if "USB" in n["dev"])
    lines.append("serial: %s%s  locks=%d  holders=%s" % (
        " ".join(acm) or "none", (" +%d ttyUSB" % usb) if usb else "", len(j["locks"]),
        "; ".join(j["holders"]) if j["holders"] else "none"))
    lines.append("nfs: home=%s validation=%s  deploy: %s" % (
        j["nfs"]["home"] or "?", "yes" if j["nfs"]["validation"] else "no", ", ".join(j["deployments"]) or "-"))
    lines.append("fw_ctl: opt_atl=%s inv=%s  bmc: %s" % (
        "yes" if j["fw_ctl"]["opt_atl"] else "no", j["fw_ctl"]["inventory"],
        " ".join("%s=%s" % kv for kv in j["bmc"].items()) or "none resolved"))
    dm = j["dmesg"]
    lines.append("dmesg: last=%s  atl/el hits=%d" % (dm["last"][:90], dm["hits"]))
    lines += ["  " + h[:150] for h in dm["last_hits"]]
    return "\n".join(lines)


def cmd_probe(a, host, station):
    j = remote_probe(host)
    print(json.dumps(j, indent=1) if a.json else render_probe(j))


def cmd_devices(a, host, station):
    j = remote_probe(host, "devices")
    if a.json:
        print(json.dumps(j["devices"], indent=1))
        return
    print("%s: %d device(s) — %s" % (j["host"], len(j["devices"]), j["guess"]))
    print("\n".join(dev_line(d) for d in j["devices"]) or "  none")


# ---------------------------------------------------------------- dmesg
def parse_since(s):
    now = datetime.now()
    for fmt in ("%H:%M", "%b %d %H:%M", "%b %d %H:%M:%S"):
        try:
            t = datetime.strptime(s, fmt)
            return t.replace(year=now.year, month=now.month if fmt == "%H:%M" else t.month,
                             day=now.day if fmt == "%H:%M" else t.day)
        except ValueError:
            pass
    die("bad --since %r (use 'HH:MM' or 'Mon D HH:MM')" % s)


def dmesg_time(line):
    m = re.match(r"\[\w{3} (\w{3}\s+\d+ \d\d:\d\d:\d\d \d{4})\]", line)
    return datetime.strptime(re.sub(r"\s+", " ", m.group(1)), "%b %d %H:%M:%S %Y") if m else None


def cmd_dmesg(a, host, station):
    rc, out, err = ssh_text(host, "dmesg -T")
    if rc != 0:
        die("dmesg rc=%d %s" % (rc, err.strip()[-200:]))
    lines = out.splitlines()
    sel = lines
    if a.since:
        t0 = parse_since(a.since)
        sel = [l for l in sel if (dmesg_time(l) or datetime.min) >= t0]
    pat = re.compile(a.grep, re.I) if a.grep else None
    if pat:
        sel = [l for l in sel if pat.search(l)]
    if a.save:
        d = evidence_dir(station, a.save)
        (d / "dmesg.log").write_text(out)
        print("saved full dmesg (%d lines) -> %s" % (len(lines), d / "dmesg.log"))
    print("dmesg: %d total, %d selected (grep=%s since=%s); last %d:" % (len(lines), len(sel), a.grep or "-", a.since or "-", min(a.tail, len(sel))))
    print("\n".join(l[:200] for l in sel[-a.tail:]) if sel else "  (none)")


# ---------------------------------------------------------------- uart
def uart_location(row, port=None):
    """-> (host-for-serial, port) honoring '<station>:/dev/ttyX' in the registry."""
    u = (row or {}).get("uart", "") or ""
    if port is None and u.startswith("/dev/"):
        port = u
    if port is None and ":" in u and not u.startswith("bmc:"):
        other, port = u.split(":", 1)
        return resolve(other)[1], port
    return None, port or "/dev/ttyACM0"


def cmd_uart(a, host, station, row):
    sub = a.args[0] if a.args else "ports"
    if sub == "ports":
        rc, out, _ = ssh_text(host, r'''for n in /dev/ttyACM* /dev/ttyUSB*; do [ -e "$n" ] || continue; id=$(ls -l /dev/serial/by-id/ 2>/dev/null | awk -v b="$(basename $n)" '$NF ~ b"$"{print $9}'); sp=$(stty -F "$n" -a 2>/dev/null | grep -oE 'speed [0-9]+' | cut -d" " -f2); printf '%s %s baud=%s %s\n' "$n" "${id:-?}" "${sp:-?}" "$(stat -c %A "$n")"; done; echo "locks: $(ls /var/lock/LCK..tty* /run/lock/LCK..tty* 2>/dev/null | tr '\n' ' ')"; echo "holders: $(pgrep -af '(^|/| )(minicom|screen|picocom|socat|tio)( |$)' | grep -vE 'pgrep|bash -c' | tr '\n' ';')"''')
        print(out.strip() or "no serial nodes on %s" % host)
    elif sub == "capture":
        other_host, port = uart_location(row, a.args[1] if len(a.args) > 1 else None)
        secs = a.args[2] if len(a.args) > 2 else "60"
        h = other_host or host
        d = evidence_dir(station, a.topic or "uart")
        out_file = d / ("uart_%s.log" % port.replace("/dev/", ""))
        rc, out, err = ship(h, "uart_capture.sh", "%s %s" % (shlex.quote(port), shlex.quote(secs)), timeout=int(secs) + 40)
        out_file.write_bytes(out)
        text = out.decode("utf8", "replace")
        print("%s\n%d bytes -> %s" % (err.decode("utf8", "replace").strip(), len(out), out_file))
        if text.strip():
            ls = text.splitlines()
            print("first: %s\nlast:  %s" % (ls[0][:120], ls[-1][:120]))
        if rc not in (0,):
            print("rc=%d" % rc)
    elif sub == "console":
        u = (row or {}).get("uart", "")
        if u.startswith("bmc:"):
            oam = int(a.args[1]) if len(a.args) > 1 else 0
            print("ssh -tt -p %d root@%s      # OAM%d; 2208-09 PCIe SW, 2210-11 SDB; BMC password typed by you; exit ~." % (2200 + oam, row["bmc"], oam))
        else:
            other_host, port = uart_location(row)
            print("ssh -t %s minicom -D %s      # 115200 8N1; exit Ctrl-A X" % (other_host or host, port))
            if not u or u == "?":
                print("(registry has no uart for %s — set: pilot stations set %s uart=<nuc>:/dev/ttyACM0)" % (station, station))
    elif sub in ("bmc-ls", "bmc-pull"):
        bmc = (row or {}).get("bmc") or die("no bmc in registry for %s" % station)
        if sub == "bmc-ls":
            rc, out, err = ssh_text("root@" + bmc, "hostname; ls -la /var/log | grep -iE 'console|uart|oam|pcie|tty'; echo; ls /etc/obmc-console 2>/dev/null", batch=False)
            print(out.strip() or err.strip())
        else:
            files = [a.file] if a.file else []
            if not files and a.oam is not None:
                rc, out, _ = ssh_text("root@" + bmc, "cd /var/log && ls -1 | grep -iE 'console|uart|oam'", batch=False)
                files = [f for f in out.split() if re.search(r"(^|\D)(%d|220%d)(\D|$)" % (a.oam, a.oam), f)]
            files or die("no file selected (--file F, or --oam N matched nothing; see uart bmc-ls)")
            d = evidence_dir(station, a.topic or "bmc_uart")
            for f in files:
                rc = subprocess.run(["scp", "-q", "-o", "ControlMaster=auto", "-o", "ControlPath=%s/cm-%%C" % PILOT_DIR,
                                     "-o", "ControlPersist=300", "root@%s:/var/log/%s" % (bmc, f), str(d / ("bmc_" + f))]).returncode
                print("%s -> %s rc=%d" % (f, d / ("bmc_" + f), rc))
    else:
        die("uart: ports | capture [PORT] [SECS] | console [OAM] | bmc-ls | bmc-pull")


def cmd_bmc(a, host, station, row):
    bmc = (row or {}).get("bmc") or die("no bmc in registry for %s" % station)
    sub = a.args[0] if a.args else "versions"
    cmd = {"versions": "oem_show_version", "fru": "oem_fruid-util %s" % (a.args[1] if len(a.args) > 1 else "EA100_UBB")}.get(sub) or die("bmc: versions | fru [BMC_DC_SCM|EA100_UBB]")
    rc, out, err = ssh_text("root@" + bmc, cmd, batch=False)
    print(out.strip() or err.strip())


# ---------------------------------------------------------------- run / status
def cmd_run(a, host, station):
    cmd = a.args[0] if a.args else die("run 'CMD'")
    if a.detach:
        tag = shlex.quote(a.detach)
        rc, out, err = ssh_text(host, "mkdir -p ~/.pilot/jobs/%s && cd ~ && (setsid nohup bash -lc %s > ~/.pilot/jobs/%s/run.log 2>&1 < /dev/null & echo $! > ~/.pilot/jobs/%s/pid) && cat ~/.pilot/jobs/%s/pid" % (tag, shlex.quote(cmd), tag, tag, tag))
        print("detached pid=%s log=~/.pilot/jobs/%s/run.log  (poll: pilot -t %s status %s)" % (out.strip(), a.detach, station, a.detach))
        return
    rc, out, err = ssh_text(host, cmd, login=True, timeout=a.timeout)
    ls = (out + err).splitlines()
    print("rc=%d lines=%d%s" % (rc, len(ls), " (showing last %d)" % a.tail if len(ls) > a.tail else ""))
    print("\n".join(l[:200] for l in ls[-a.tail:]))


def cmd_status(a, host, station):
    tag = shlex.quote(a.args[0]) if a.args else die("status TAG")
    rc, out, err = ssh_text(host, "d=~/.pilot/jobs/%s; p=$(cat $d/pid 2>/dev/null); if [ -n \"$p\" ] && kill -0 $p 2>/dev/null; then echo STATE=RUNNING pid=$p; else echo STATE=DONE; fi; echo lines=$(wc -l < $d/run.log 2>/dev/null); tail -n %d $d/run.log 2>/dev/null" % (tag, a.tail))
    print(out.strip() or err.strip())


# ---------------------------------------------------------------- workspaces / octl exec
WS_PAYLOAD = r'''
import glob, json, os
home = os.path.expanduser("~")
seen = set()
for m in sorted(glob.glob(home + "/projects/*/*/.repo/metadata.json") + glob.glob(home + "/projects/*/*/*/.repo/metadata.json")):
    try:
        d = json.load(open(m))
    except Exception:
        continue
    ws = os.path.dirname(os.path.dirname(m))
    if ws in seen:
        continue
    seen.add(ws)
    man = d.get("manifest", "?")
    flag = "*" if man.startswith("integration") else "-"
    print("%s %-52s %-20s %-26s rev=%s arts-only=%s synced=%s" % (flag, ws.replace(home + "/", "~/"), man, d.get("manifest_version") or "-", d.get("manifest_revision") or "-", d.get("s3_only"), d.get("synced")))
'''


def list_ws(host):
    rc, out, err = ssh(host, "python3 -", stdin=WS_PAYLOAD.encode())
    return out.decode("utf8", "replace").strip().splitlines()


def cmd_ws(a, host, station):
    rows = list_ws(host)
    print("\n".join(rows) or "no octl workspaces under ~/projects on %s" % host)
    if not any(r.startswith("*") for r in rows):
        print("(* = integration manifest; none found. init one: cd ~/projects/<prj>/<branch> && octl ws init -p iridium -m integration-ci [--artifacts-only])")


def remote_path(p):
    """Quote a path for the remote shell, keeping a leading ~ expandable."""
    if p == "~":
        return '"$HOME"'
    if p.startswith("~/"):
        return '"$HOME"' + shlex.quote("/" + p[2:])
    return shlex.quote(p)


def cmd_exec(a, host, station):
    if not a.args:
        die("exec 'STAR-CMD' [--ws PATH]")
    try:
        argv = shlex.split(" ".join(a.args))
    except ValueError as e:
        die("cannot parse STAR-CMD (%s)" % e)
    if not argv:
        die("empty STAR-CMD")
    ws = a.ws
    if not ws:
        integ = [r.split()[1] for r in list_ws(host) if r.startswith("*")]
        ws = integ[0] if integ else die("no integration workspace on %s; pass --ws PATH" % host)
        print("ws=%s" % ws)
    # octl ws exec takes an argv, not a shell string: quote each word separately
    # or octl reads the whole line as the star-target name.
    star = " ".join(shlex.quote(t) for t in argv)
    rc, out, err = ssh_text(host, "cd %s && octl ws exec %s" % (remote_path(ws), star), login=True, timeout=a.timeout)
    ls = (out + err).splitlines()
    print("rc=%d lines=%d%s" % (rc, len(ls), " (showing last %d)" % a.tail if len(ls) > a.tail else ""))
    print("\n".join(l[:200] for l in ls[-a.tail:]))


# ---------------------------------------------------------------- nfs
def cmd_nfs(a, host, station):
    sub = a.args[0] if a.args else "deployments"
    arg = a.args[1] if len(a.args) > 1 else None
    if sub == "deployments":
        n = int(arg or 5)
        cmd = "ls -lt --time-style=+%%Y-%%m-%%dT%%H:%%M ~/a1_power_on_images | awk 'NR>1 && NR<=%d{print $6\"  \"$7}'" % (n + 1)
    elif sub == "notes":
        cmd = "f=~/a1_power_on_images/%s; [ -f $f/CHANGE_NOTES.md ] && f=$f/CHANGE_NOTES.md || f=$f/asic/CHANGE_NOTES.md; head -n 30 $f" % shlex.quote(arg or die("nfs notes DIR"))
    elif sub == "hash":
        cmd = "stat -c '%%s bytes  %%y' %s && sha256sum %s" % (shlex.quote(arg), shlex.quote(arg or die("nfs hash PATH")))
    elif sub == "ls":
        cmd = "ls -la %s | head -n 40" % shlex.quote(arg or "~")
    else:
        die("nfs: deployments [N] | notes DIR | hash PATH | ls PATH")
    rc, out, err = ssh_text(host, cmd)
    print(out.strip() or err.strip() or "rc=%d" % rc)


# ---------------------------------------------------------------- evidence
def evidence_dir(station, topic):
    d = EVIDENCE / station / ("%s_%s" % (utc("%d%m%Y_%H%M%S"), re.sub(r"[^A-Za-z0-9_-]", "_", topic)))
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_evidence(a, host, station, row):
    topic = a.args[0] if a.args else die("evidence TOPIC")
    d = evidence_dir(station, topic)
    j = remote_probe(host)
    (d / "probe.json").write_text(json.dumps(j, indent=1))
    (d / "probe.txt").write_text(render_probe(j) + "\n")
    for name, cmd in [("dmesg.log", "dmesg -T"),
                      ("lspci.txt", "lspci -d 208b: -nn; lspci -d 1d0f: -nn; echo; lspci -tv; for d in $(lspci -d 208b: | cut -d' ' -f1) $(lspci -d 1d0f: | cut -d' ' -f1); do echo; lspci -vvs $d; done"),
                      ("lsmod.txt", "lsmod; echo; dkms status 2>/dev/null")]:
        rc, out, err = ssh(host, cmd)
        (d / name).write_bytes(out or err)
    if a.bmc and row and row.get("bmc"):
        rc, out, err = ssh_text("root@" + row["bmc"], "hostname; date -u; ls -la /var/log; ls /etc/obmc-console 2>/dev/null", batch=False)
        (d / "bmc_var_log.txt").write_text(out or err)
    if a.tty:
        other_host, port = uart_location(row, a.tty)
        rc, out, err = ship(other_host or host, "uart_capture.sh", "%s %d" % (shlex.quote(port), a.secs), timeout=a.secs + 40)
        (d / ("uart_%s.log" % port.replace("/dev/", ""))).write_bytes(out)
        (d / "uart.stderr").write_bytes(err)
    sums = ["%s  %s" % (hashlib.sha256(p.read_bytes()).hexdigest(), p.name) for p in sorted(d.iterdir())]
    (d / "sha256sums").write_text("\n".join(sums) + "\n")
    print("%s\n  %s" % (d, "  ".join("%s(%d)" % (p.name, p.stat().st_size) for p in sorted(d.iterdir()))))


# ---------------------------------------------------------------- memoir / pitfalls / sessions / stations
def insert_under_heading(path, heading_re, bullet, create_heading, newest_first=True):
    text = path.read_text()
    lines = text.splitlines()
    idx = next((i for i, l in enumerate(lines) if re.match(heading_re, l, re.I)), None)
    if idx is None:
        lines += ["", create_heading, bullet]
    elif newest_first:
        lines.insert(idx + 1, bullet)
    else:
        end = next((i for i in range(idx + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        while end > idx + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, bullet)
    path.write_text("\n".join(lines) + "\n")


def cmd_memoir(a, station):
    text = a.args[0] if a.args else die("memoir 'TEXT'")
    insert_under_heading(MEMOIR, r"^### %s\b" % re.escape(station), "- **%s** — %s" % (utc("%Y-%m-%d %H:%MZ"), text), "### %s" % station)
    print("memoir: +1 under ### %s" % station)


def cmd_pitfall(a):
    theme, text = (a.args + [None, None])[:2]
    theme and text or die("pitfall THEME 'symptom → cause → fix'")
    insert_under_heading(PITFALLS, r"^## %s" % re.escape(theme), "- %s *(%s)*" % (text, utc("%Y-%m-%d")), "## %s" % theme, newest_first=False)
    print("pitfalls: +1 under ## %s" % theme)


def cmd_session(a, station, host, row):
    sub = a.args[0] if a.args else "open"
    if sub == "open":
        topic = a.args[1] if len(a.args) > 1 else die("session open TOPIC")
        SESSIONS.mkdir(exist_ok=True)
        p = SESSIONS / ("%s-%s.md" % (station, utc("%Y%m%d-%H%M")))
        p.write_text("# %s — %s — %s\n\n**Goal:** %s\n**Setup (memoir / probe):** %s / ?\n**Host:** %s  **BMC:** %s  **UART:** %s\n**Allocation:** ?\n**Devices:** ?\n\n## Actions (UTC, class, command, rc)\n\n## Evidence\n\n## Findings\n- *observed*:\n- *inferred*:\n\n## Handoffs\n\n## Memoir / pitfalls updated\n\n**Status:** open\n" % (
            station, utc("%Y-%m-%d %H:%M UTC"), topic, topic, (row or {}).get("type", "?"), host, (row or {}).get("bmc", "?"), (row or {}).get("uart", "?")))
        print(p)
    elif sub == "close":
        p = Path(a.args[1]) if len(a.args) > 1 else die("session close FILE")
        p.write_text(p.read_text().replace("**Status:** open", "**Status:** closed (%s)" % utc()))
        print("closed %s" % p)
    else:
        die("session open TOPIC | close FILE")


def cmd_stations(a):
    rows = load_stations()
    if a.args and a.args[0] == "set":
        key = a.args[1] if len(a.args) > 1 else die("stations set STATION k=v ...")
        r = rows.setdefault(key, dict(station=key, host="?", type="?", bmc="?", uart="?", notes="", verified=""))
        for kv in a.args[2:]:
            k, v = kv.split("=", 1)
            k in r or die("unknown field %s" % k)
            r[k] = v
        r["verified"] = utc("%Y-%m-%d")
        save_stations(rows)
        print("updated %s: %s" % (key, "  ".join("%s=%s" % (k, r[k]) for k in ["host", "type", "bmc", "uart"])))
        return
    for r in rows.values():
        print("%-6s %-7s %-29s bmc=%-17s uart=%-15s %s" % (r["station"], r["type"], r["host"], r["bmc"], r["uart"], r["notes"][:60]))


# ---------------------------------------------------------------- main
def main():
    global VERBOSE
    ap = argparse.ArgumentParser(prog="pilot", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-t", "--target")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--grep", default=DMESG_SIG)
    ap.add_argument("--tail", type=int, default=40)
    ap.add_argument("--since")
    ap.add_argument("--save")
    ap.add_argument("--topic")
    ap.add_argument("--file")
    ap.add_argument("--oam", type=int)
    ap.add_argument("--bmc", action="store_true")
    ap.add_argument("--tty")
    ap.add_argument("--secs", type=int, default=60)
    ap.add_argument("--detach")
    ap.add_argument("--ws")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    VERBOSE = a.verbose
    if a.cmd == "stations":
        return cmd_stations(a)
    if a.cmd == "pitfall":
        return cmd_pitfall(a)
    station, host, row = resolve(a.target)
    table = {"probe": lambda: cmd_probe(a, host, station), "devices": lambda: cmd_devices(a, host, station),
             "dmesg": lambda: cmd_dmesg(a, host, station), "uart": lambda: cmd_uart(a, host, station, row),
             "bmc": lambda: cmd_bmc(a, host, station, row), "run": lambda: cmd_run(a, host, station),
             "status": lambda: cmd_status(a, host, station), "nfs": lambda: cmd_nfs(a, host, station),
             "ws": lambda: cmd_ws(a, host, station), "exec": lambda: cmd_exec(a, host, station),
             "evidence": lambda: cmd_evidence(a, host, station, row), "session": lambda: cmd_session(a, station, host, row),
             "memoir": lambda: cmd_memoir(a, station)}
    (table.get(a.cmd) or (lambda: die("unknown cmd %s\n%s" % (a.cmd, __doc__))))()


if __name__ == "__main__":
    main()
