#!/usr/bin/env python3
"""Remote probe payload — runs ON the lab host (python3, stdlib, read-only, no sudo).
Shipped by pilot.py over stdin:  ssh host python3 - [devices]   -> one JSON object.
"""
import glob
import json
import os
import re
import socket
import subprocess
import sys

OUR_VENDORS = {"0x208b", "0x1d0f"}
SWITCH_IDS = {("0x1000", "0xc040"), ("0x1000", "0x00b2")}
SIG = re.compile(r"atl |el_pci|el_ctl|el_accel|208b|1d0f|7596", re.I)
HOLDERS = r"(^|/| )(minicom|screen|picocom|socat|tio|fwloader|fw_host_cli|pytest)( |$)|fw_ctl|octl ws (test|exec)|obmc-console"


def sh(cmd, timeout=20):
    try:
        return subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=timeout, universal_newlines=True).stdout
    except Exception:
        return ""


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return default


def human(n):
    for unit in "BKMGT":
        if n < 1024:
            return "%d%s" % (n, unit)
        n //= 1024
    return "%dP" % n


def pci_devices():
    devs, switch = [], False
    for d in sorted(glob.glob("/sys/bus/pci/devices/*")):
        vid, did = read(d + "/vendor"), read(d + "/device")
        if (vid, did) in SWITCH_IDS:
            switch = True
        if vid not in OUR_VENDORS:
            continue
        drv = os.path.basename(os.readlink(d + "/driver")) if os.path.islink(d + "/driver") else None
        try:
            with open(d + "/config", "rb") as f:
                cfg = f.read(64)
            mem_en = bool((cfg[4] | (cfg[5] << 8)) & 0x2)
        except Exception:
            mem_en = None
        res = read(d + "/resource").splitlines()

        def bar(i):
            try:
                s, e, _ = res[i].split()
                s, e = int(s, 16), int(e, 16)
                return human(e - s + 1) if e > s else None
            except Exception:
                return None

        devs.append(dict(bdf=os.path.basename(d)[5:], vid=vid[2:], did=did[2:],
                         speed=read(d + "/current_link_speed"), width=read(d + "/current_link_width"),
                         max_speed=read(d + "/max_link_speed"), max_width=read(d + "/max_link_width"),
                         driver=drv, mem_en=mem_en, bar0=bar(0), bar2=bar(2)))
    return devs, switch


def serial_nodes():
    byid = {}
    for p in glob.glob("/dev/serial/by-id/*"):
        byid[os.path.realpath(p)] = os.path.basename(p)
    nodes = []
    for n in sorted(glob.glob("/dev/ttyACM*") + glob.glob("/dev/ttyUSB*")):
        nodes.append(dict(dev=n, id=byid.get(n), readable=os.access(n, os.R_OK)))
    locks = glob.glob("/var/lock/LCK..tty*") + glob.glob("/run/lock/LCK..tty*")
    return nodes, locks


def allocation():
    motd = read("/etc/motd")
    m = re.search(r"allocated to:\s*\n\s*(\S+)", motd)
    return m.group(1) if m else None


def uptime():
    try:
        s = int(float(read("/proc/uptime").split()[0]))
        return "%dd%02dh" % (s // 86400, (s % 86400) // 3600)
    except Exception:
        return "?"


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else "all"
    devs, switch = pci_devices()
    out = dict(host=socket.gethostname(), devices=devs, switch=switch)
    nodes, locks = serial_nodes()
    n = len(devs)
    if n == 0:
        guess = "nuc(no device, serial present)" if nodes else "no-device"
    elif n == 1:
        guess = "bench(stand-alone|riser — memoir declares)"
    elif switch:
        guess = "els-tray %d/8" % n
    else:
        guess = "multi-device %d (no Broadcom switch)" % n
    out["guess"] = guess
    if only == "devices":
        json.dump(out, sys.stdout)
        return
    home = os.path.expanduser("~")
    dep_dir = os.path.join(home, "a1_power_on_images")
    deps = []
    if os.path.isdir(dep_dir):
        ents = [(os.path.getmtime(os.path.join(dep_dir, e)), e) for e in os.listdir(dep_dir)]
        deps = [e for _, e in sorted(ents, reverse=True)[:3]]
    inv = "/var/log/atl/fw_ctl/pcie_inventory.json"
    bmc = {}
    h = socket.gethostname().split(".")[0]
    for cand in {"bmc-" + h, "bmc-" + h.replace("srv", "els")}:
        try:
            bmc[cand] = socket.gethostbyname(cand)
        except Exception:
            pass
    dm = sh("dmesg -T", 25).splitlines()
    hits = [l for l in dm if SIG.search(l)]
    out.update(
        kernel=os.uname().release, uptime=uptime(), user=os.environ.get("USER", "?"),
        alloc=allocation(), sudo=(sh("sudo -n true 2>&1") == ""),
        serial=nodes, locks=locks,
        holders=[l for l in sh("pgrep -af '%s'" % HOLDERS).splitlines() if l.strip() and "pgrep" not in l and "bash -c" not in l],
        nfs=dict(home=sh("findmnt -T %s -n -o SOURCE,FSTYPE" % home).strip(),
                 validation=os.path.ismount("/validation")),
        deployments=deps,
        fw_ctl=dict(opt_atl=os.path.isdir("/opt/atl/fw/scripts/fw_ctl"),
                    inventory=("readable" if os.access(inv, os.R_OK) else "root-only") if os.path.exists(inv) else "absent"),
        bmc=bmc,
        dmesg=dict(last=dm[-1] if dm else "", hits=len(hits), last_hits=hits[-4:]),
    )
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
