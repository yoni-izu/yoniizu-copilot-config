#!/usr/bin/env python3
"""fw-flash: discover flash paths on a lab machine and flash an image onto its OAMs.

Every machine is an mctl target (``mctl -t T dest`` for the host, ``mctl -t T bmc``
for its BMC). Delivery methods use READ-BACK, not the write tool's exit code:

* ``bmc``      ``bmccli flash update`` on the tray BMC (queued in the orchestrator
               daemon); progress is the daemon's ``journalctl -u orchestrator``;
               read-back ``bmccli flash version -f`` (live FPT header re-read).
* ``pcie``     ``fw_ctl flash write --transport pcie`` on the host (root, no el_*
               driver bound); read-back through the BMC when the tray has one.
* ``spi-ftdi`` ``fw_ctl flash write --transport ftdi-spi`` on the host.
* ``spi-bmc``  ``fwloader -m flash -d /dev/mtd/soc-nv-flash`` on the BMC; read-back
               is a flash dump compared (SHA-256 over the image length) with the image.
* ``redfish``  A1 whole-tray OEM upload + UpdateA1 over verified HTTPS (CLI only).
               Reports written, not verified, until a separate boot check.

``discover`` is read-only. ``run`` prints a human log on stdout (the job log the
portal shows, including every orchestrator journal line) and, with ``--events``,
appends one JSON object per line describing phases and per-OAM progress.

An OAM is reported ``verified`` only when read-back shows the image's build in
every FPT AND there is evidence the write happened (the orchestrator/tool reported
completion, or the build changed). Re-flashing an identical build with no
completion evidence is ``unverified``, never a false success.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import shlex
import shutil
import signal
import ssl
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

METHODS = ("bmc", "pcie", "spi-ftdi", "spi-bmc")
CLI_METHODS = (*METHODS, "redfish")
METHOD_LABELS = {"bmc": "BMC (bmccli flash update)", "pcie": "PCIe (fw_ctl)",
                 "spi-ftdi": "SPI over FTDI (fw_ctl)", "spi-bmc": "SPI from the BMC (fwloader MTD)",
                 "redfish": "Redfish (A1 whole tray)"}
FW_CTL_DIR = "/opt/atl/fw/scripts"
FW_CTL_SETUP = FW_CTL_DIR + "/fw_ctl/setup_fw_ctl_env.sh"
BMC_MTD = "/dev/mtd/soc-nv-flash"
VERSION_RE = re.compile(r"\b\d+\.\d+\.\d+-\d{8}-\d{6}\b")
STAMP_RE = re.compile(r"\d{8}-\d{6}")
DEV_RE = re.compile(r"^dev(\d+)\b(.*)$")
TAG_RE = re.compile(r"\[[A-Za-z_]+:(\d+)\]|\bdev(?:ice)?\s*(\d+)\b", re.I)
PCT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
FAIL_RE = re.compile(r"\b(fail(?:ed|ure)?|error|abort(?:ed)?|mismatch|timed? ?out)\b", re.I)
DONE_RE = re.compile(r"\b(done|complete[d]?|success(?:ful(?:ly)?)?|finished|succeeded)\b", re.I)
FLASHY_RE = re.compile(r"flash|nor|update|program|fwu|erase|write|fpt", re.I)
TIMEOUT = 30 * 60
POLL = float(os.environ.get("FW_FLASH_POLL", "3"))
WRITTEN = ("written",)
REDFISH_TASKS = "/redfish/v1/TaskService/Tasks"
REDFISH_INVENTORY = "/redfish/v1/UpdateService/FirmwareInventory"
REDFISH_CHASSIS = "/redfish/v1/Chassis"
REDFISH_UPLOAD = "/redfish/v1/UpdateService/Oem/EL/FirmwareUpload/A1"
REDFISH_UPDATE = "/redfish/v1/UpdateService/Actions/Oem/ELUpdateService.UpdateA1"
REDFISH_IMAGE = "/tmp/a1_update.img"
REDFISH_LOCK = "/tmp/fw-flash-redfish.lock"
REDFISH_TASK_ID = r"(?:(?:OAM_)?(?:TASK_)?\d+)"
REDFISH_TERMINAL = {"Completed", "Exception", "Killed", "Cancelled", "Canceled",
                    "Interrupted"}
REDFISH_WRITTEN = re.compile(r"\[spi_flash:\d+\].*\bflash (?:update|write|programming) "
                             r"(?:done|completed|succeeded|successful)\b", re.I)


class FlashError(Exception):
    pass


class RedfishHTTPError(FlashError):
    def __init__(self, method: str, path: str, status: int):
        super().__init__(f"Redfish {method} {path}: HTTP {status}")
        self.status = status


class Cancelled(Exception):
    pass


def mctl() -> str:
    return os.environ.get("FW_FLASH_MCTL") or shutil.which("mctl") or str(Path.home() / "bin" / "mctl")


def sh(argv, *, stdin=None, timeout=120):
    try:
        p = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise FlashError(f"timed out after {timeout}s: {' '.join(argv[:4])} ...") from e
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _last_error(err: str, rc: int) -> str:
    lines = [l.strip() for l in err.splitlines() if l.strip()
             and not re.search(r"^[*#=|]|authori[sz]ed|allocat|permanently added", l.strip(), re.I)]
    return lines[-1] if lines else f"rc={rc}"


def bmc_sh(target, script, **kw):
    return sh([mctl(), "-t", target, "bmc", script], **kw)


def host_dest(target) -> str:
    rc, out, err = sh([mctl(), "-t", target, "dest"], timeout=30)
    dest = out.strip().splitlines()[-1] if out.strip() else ""
    if rc != 0 or not dest:
        raise FlashError(f"'{target}' is not a registered mctl target ({_last_error(err, rc)})")
    return dest


def host_sh(target, script, dest=None, **kw):
    dest = dest or host_dest(target)
    return sh(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
               "-o", "ServerAliveInterval=15", dest, script], **kw)


def redfish_host(target: str) -> str:
    rc, out, err = sh([mctl(), "targets"], timeout=30)
    if rc:
        raise FlashError(f"cannot read the mctl target registry ({_last_error(err, rc)})")
    for line in out.splitlines():
        row = line.lstrip("* ").split()
        if len(row) == 5 and row[:2] == [target, "remote"]:
            host = row[4].rsplit("@", 1)[-1]
            if re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]*", host):
                return host
    raise FlashError("Redfish requires a registered mctl target name with a BMC hostname")


class Redfish:
    def __init__(self, host: str, ca: str | None):
        user, password = os.environ.get("BMC_USER"), os.environ.get("BMC_PASSWORD")
        if not user or not password or ":" in user:
            raise FlashError("set BMC_USER and BMC_PASSWORD from the approved credential store")
        self.host = host
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode("ascii")
        try:
            self.context = ssl.create_default_context()
            if ca:
                self.context.load_verify_locations(cafile=Path(ca).expanduser())
        except OSError as e:
            raise FlashError(f"cannot load Redfish TLS trust: {e}") from e

    def request(self, method, path, *, body=None, headers=None, timeout=30):
        conn = http.client.HTTPSConnection(self.host, context=self.context, timeout=timeout)
        try:
            conn.request(method, path, body=body, headers={
                "Authorization": self.auth, "Accept": "application/json", **(headers or {})})
            resp = conn.getresponse()
            if not 200 <= resp.status < 300:
                raise RedfishHTTPError(method, path, resp.status)
            raw, location = resp.read(), resp.getheader("Location")
        except (OSError, http.client.HTTPException) as e:
            raise FlashError(f"Redfish {method} {path}: {e}") from e
        finally:
            conn.close()
        # A successful action can return no JSON; locate its task via TaskService.
        try:
            data = json.loads(raw) if raw.strip() else None
        except (ValueError, UnicodeError):
            data = None
        if method == "GET" and not isinstance(data, dict):
            raise FlashError(f"Redfish GET {path}: expected a JSON object")
        return data, location

    def members(self, path, *, timeout=30):
        body, _ = self.request("GET", path, timeout=timeout)
        rows = body.get("Members")
        if (not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows)
                or body.get("Members@odata.nextLink") or body.get("@odata.nextLink")
                or body.get("Members@odata.count", len(rows)) != len(rows)):
            raise FlashError(f"Redfish {path}: incomplete or invalid collection")
        return rows

    @staticmethod
    def member_name(row):
        ref = row.get("@odata.id")
        return ref.rstrip("/").rsplit("/", 1)[-1] if isinstance(ref, str) else ""

    def slots(self) -> set[int]:
        fitted = {}
        for row in self.members(REDFISH_INVENTORY):
            name = self.member_name(row)
            match = re.fullmatch(r"OAM(\d*)_XDPE0", name, re.I)
            if not match:
                continue
            idx = int(match[1] or "0")
            if idx in fitted:
                raise FlashError(f"Redfish inventory repeated OAM {idx}")
            if "Version" not in row:
                row, _ = self.request("GET", f"{REDFISH_INVENTORY}/{name}")
            version = row.get("Version")
            if not isinstance(version, str) or not version.strip():
                raise FlashError(f"Redfish inventory has no VR version for OAM {idx}")
            fitted[idx] = version.strip().upper() not in ("NA", "N/A")
        if fitted:
            return {idx for idx, present in fitted.items() if present}
        return {int(match[1]) for row in self.members(REDFISH_CHASSIS)
                if (match := re.fullmatch(r"OAM_(\d+)", self.member_name(row), re.I))}

    def task_ids(self, *, timeout=30) -> set[str]:
        ids = {self.member_name(row) for row in self.members(REDFISH_TASKS, timeout=timeout)}
        if any(not re.fullmatch(REDFISH_TASK_ID, tid, re.I) for tid in ids):
            raise FlashError("Redfish task queue returned an unrecognized task ID")
        return ids

    def task(self, tid, *, timeout=30):
        body, _ = self.request("GET", f"{REDFISH_TASKS}/{tid}", timeout=timeout)
        return body

    def idle_tasks(self) -> set[str]:
        ids = self.task_ids()
        for tid in sorted(ids, key=lambda x: int(x.rsplit("_", 1)[-1]), reverse=True):
            state = self.task(tid).get("TaskState")
            if not isinstance(state, str) or state not in REDFISH_TERMINAL:
                raise FlashError(f"Redfish task queue not idle: {tid} ({state})")
        return ids

    def returned_task(self, body, location, before) -> str | None:
        refs = [location, body.get("@odata.id") if isinstance(body, dict) else None]
        found = set()
        for ref in refs:
            if not isinstance(ref, str) or not ref:
                continue
            try:
                url = urlsplit(ref)
            except ValueError as e:
                raise FlashError("invalid Redfish task reference") from e
            host = self.host.lower()
            if (url.scheme or url.netloc) and (
                    url.scheme != "https" or url.netloc.lower() not in (host, f"{host}:443")):
                raise FlashError("Redfish returned a task reference outside the registered BMC")
            match = re.fullmatch(rf"{REDFISH_TASKS}/({REDFISH_TASK_ID})/?", url.path, re.I)
            if match:
                found.add(match[1])
        if len(found) > 1 or found & before:
            raise FlashError("Redfish returned conflicting or pre-existing task references")
        return next(iter(found), None)


# ----------------------------------------------------------------- parsing
def image_version(path: Path) -> str:
    """Build ID embedded in the image (``manifest version: X``); most frequent wins."""
    found = Counter(m.group(0).decode() for m in re.finditer(
        rb"\b\d+\.\d+\.\d+-\d{8}-\d{6}\b", path.read_bytes()))
    return found.most_common(1)[0][0] if found else ""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_dev_kv(text: str) -> dict[int, dict]:
    """bmccli ``devN[k=v ...]`` / ``devN k=v ...`` / ``devN=value`` lines -> {N: {...}}.
    Repeated ``build=`` (one line per FPT copy) accumulate in ``builds``."""
    out: dict[int, dict] = {}
    for raw in text.splitlines():
        m = DEV_RE.match(raw.strip())
        if not m:
            continue
        row = out.setdefault(int(m.group(1)), {})
        rest = m.group(2).strip()
        if rest.startswith("="):
            row.setdefault("values", []).append(rest[1:].strip())
            continue
        for k, v in re.findall(r"([A-Za-z_]+)=([^\s\]]+)", rest):
            v = v.rstrip(",")
            if k == "build":
                row.setdefault("builds", []).append(v)
                row.setdefault("build", v)
            else:
                row[k] = v
    return out


def same_build(running: str, expected: str) -> bool:
    """Running FW reports ``<fw_version>-<stamp>``, the image ``<manifest>-<stamp>``;
    the build date stamp identifies the build on both sides."""
    a, b = STAMP_RE.search(running or ""), STAMP_RE.search(expected or "")
    return bool(a and b and a.group(0) == b.group(0))


def journal_oam(line: str) -> int | None:
    m = TAG_RE.search(line)
    if not m:
        return None
    return int(m.group(1) if m.group(1) is not None else m.group(2))


def classify_journal(line: str):
    """-> (oam, kind, pct) for a flash-related journal line, else None."""
    oam = journal_oam(line)
    if oam is None or not FLASHY_RE.search(line):
        return None
    pct = PCT_RE.search(line)
    kind = "fail" if FAIL_RE.search(line) else ("done" if DONE_RE.search(line) else "progress")
    return oam, kind, (min(100, int(float(pct.group(1)))) if pct else None)


# --------------------------------------------------------------- discovery
BMC_PROBE = ("echo __STATUS__; bmccli device status -d all 2>&1; "
             "echo __VERSION__; bmccli flash version -d all 2>&1; "
             "echo __OAM__; bmccli device oam-status -d all 2>&1; "
             f"echo __MTD__; [ -e {BMC_MTD} ] && echo yes || echo no; "
             "echo __FWLOADER__; command -v fwloader 2>/dev/null || "
             "ls /opt/atl/fw/tools/fwloader/bmc/fwloader 2>/dev/null; echo; "
             "echo __TMP__; df -k /tmp 2>/dev/null | tail -n 1")

HOST_PROBE = (f"echo __FWCTL__; [ -f {FW_CTL_SETUP} ] && echo yes || echo no; "
              "echo __UID__; id -u; "
              "echo __SUDO__; sudo -n true 2>/dev/null && echo yes || echo no; "
              "echo __ELMOD__; lsmod 2>/dev/null | awk '/^el_/{print $1}' | tr '\\n' ' '; echo; "
              "echo __BDF__; lspci -D -d 1d0f: 2>/dev/null | awk '{print $1}' | tr '\\n' ' '; echo; "
              "echo __FTDI__; lsusb 2>/dev/null | grep -ci ' 0403:'; "
              "echo __END__")


def _sections(text: str) -> dict[str, str]:
    out, cur = {}, None
    for line in text.splitlines():
        m = re.fullmatch(r"__([A-Z]+)__", line.strip())
        if m:
            cur = m.group(1)
            out[cur] = ""
        elif cur:
            out[cur] += line + "\n"
    return out


def probe_bmc(target) -> dict:
    try:
        rc, out, err = bmc_sh(target, BMC_PROBE, timeout=60)
    except FlashError as e:
        return {"ok": False, "error": str(e)}
    if "__STATUS__" not in out:
        return {"ok": False, "error": _last_error(err or out, rc)}
    s = _sections(out)
    status, vers, oam = (parse_dev_kv(s.get(k, "")) for k in ("STATUS", "VERSION", "OAM"))
    oams = []
    for idx in sorted(set(status) | set(vers) | set(oam)):
        v, o = vers.get(idx, {}), oam.get(idx, {})
        oams.append({"index": idx, "label": f"OAM {idx}",
                     "state": status.get(idx, {}).get("state", ""),
                     "version": v.get("version", ""), "build": v.get("build", ""),
                     "builds": v.get("builds", []),
                     "boot": o.get("boot", ""), "rdy": o.get("rdy", ""),
                     "recovery": o.get("recovery", ""), "fatal": o.get("fatal", "")})
    tmp = (s.get("TMP") or "").split()
    return {"ok": True, "oams": oams, "mtd": (s.get("MTD") or "").strip() == "yes",
            "fwloader": (s.get("FWLOADER") or "").strip(),
            "tmp_free_kb": int(tmp[3]) if len(tmp) > 3 and tmp[3].isdigit() else None}


def probe_host(target) -> dict:
    try:
        dest = host_dest(target)
        rc, out, err = host_sh(target, HOST_PROBE, dest=dest, timeout=60)
    except FlashError as e:
        return {"ok": False, "error": str(e)}
    if "__END__" not in out:
        return {"ok": False, "error": _last_error(err, rc)}
    s = _sections(out)
    get = lambda k: (s.get(k) or "").strip()  # noqa: E731
    return {"ok": True, "dest": dest, "fw_ctl": get("FWCTL") == "yes",
            "root": get("UID") == "0", "sudo": get("SUDO") == "yes",
            "el_modules": get("ELMOD").split(), "bdfs": get("BDF").split(),
            "ftdi": int(get("FTDI")) if get("FTDI").isdigit() else 0}


def host_reason(host: dict, method: str) -> str:
    if not host.get("ok"):
        return host.get("error") or "host unreachable"
    if not host["fw_ctl"]:
        return f"fw_ctl is not installed on the host ({FW_CTL_SETUP} missing)"
    if not (host["root"] or host["sudo"]):
        return "flash writes need root and passwordless sudo is not available on the host"
    if method == "pcie" and host["el_modules"]:
        return "el_* driver bound (" + ", ".join(host["el_modules"]) + "); the PCIe transport refuses"
    if method == "pcie" and not host["bdfs"]:
        return "no 1d0f: PCIe endpoint enumerated on the host"
    if method == "spi-ftdi" and not host["ftdi"]:
        return "no FTDI (0403:) USB device on the host"
    return ""


def bmc_mtd_reason(bmc: dict) -> str:
    if not bmc.get("ok"):
        return bmc.get("error") or "no BMC"
    if not bmc.get("mtd"):
        return f"the BMC has no {BMC_MTD} MTD label (only its own partitions)"
    if not bmc.get("fwloader"):
        return "fwloader is not installed on the BMC"
    return ""


def discover(target: str) -> dict:
    bmc, host = probe_bmc(target), probe_host(target)
    bmc_oams = bmc.get("oams") or []
    methods = {"bmc": {"available": bool(bmc.get("ok") and bmc_oams), "oams": bmc_oams}}
    if not methods["bmc"]["available"]:
        methods["bmc"].update(oams=[], reason=bmc.get("error") or "bmccli reported no devices")
    host_oams = bmc_oams or [{"index": i, "label": f"OAM {i}", "bdf": b, "state": "", "build": ""}
                             for i, b in enumerate(host.get("bdfs") or [])]
    for m in ("pcie", "spi-ftdi"):
        why = host_reason(host, m)
        methods[m] = {"available": not why, "oams": [] if why else host_oams,
                      **({"reason": why} if why else {})}
    why = bmc_mtd_reason(bmc)
    methods["spi-bmc"] = {"available": not why, **({"reason": why} if why else {}),
                          "oams": [] if why else [{"index": 0, "label": "soc-nv-flash",
                                                   "state": "", "build": ""}]}
    for k, v in methods.items():
        v["label"] = METHOD_LABELS[k]
    return {"target": target, "checked": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "bmc": {k: v for k, v in bmc.items() if k != "oams"}, "host": host,
            "methods": {m: methods[m] for m in METHODS},
            "default": next((m for m in METHODS if methods[m]["available"]), "")}


# --------------------------------------------------------------------- run
class Reporter:
    """Human log on stdout + machine events in --events (JSON lines)."""

    def __init__(self, events: str | None):
        self.path = Path(events) if events else None
        self.lock = threading.Lock()

    def emit(self, kind: str, **kw):
        if not self.path:
            return
        rec = json.dumps({"t": round(time.time(), 3), "type": kind, **kw}, ensure_ascii=False)
        with self.lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(rec + "\n")

    def say(self, msg: str, oam=None, event=True):
        with self.lock:
            print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
        if event:
            self.emit("log", line=msg, **({"oam": oam} if oam is not None else {}))

    def step(self, phase: str, msg: str):
        self.emit("phase", phase=phase, message=msg)
        self.say(f"== {phase}: {msg}", event=False)

    def oam(self, idx, state, msg="", pct=None, **extra):
        self.emit("oam", oam=idx, state=state, message=msg,
                  **({"pct": pct} if pct is not None else {}), **extra)
        if msg:
            self.say(f"OAM {idx}: {state} - {msg}", event=False)


class JournalFollower:
    """``journalctl -u orchestrator -f`` on the BMC, copied line-by-line into the job
    log and classified per OAM (``[spi_flash:N]`` / ``[cpld:N]`` / ``devN`` tags)."""

    CMD = "journalctl -u orchestrator -f -n 0 --no-pager -o short-iso"

    def __init__(self, target: str, rep: Reporter):
        self.target, self.rep = target, rep
        self.proc = None
        self.events: list[tuple[int, str, str, int | None]] = []
        self.lock = threading.Lock()
        self.lines = 0

    def start(self):
        self.proc = subprocess.Popen([mctl(), "-t", self.target, "bmc", self.CMD],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     start_new_session=True)
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()
        self.rep.say("following `journalctl -u orchestrator -f` on the BMC")

    def _pump(self):
        for raw in self.proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            self.lines += 1
            hit = classify_journal(line)
            self.rep.say(f"[journal] {line}", oam=hit[0] if hit else journal_oam(line))
            if hit:
                with self.lock:
                    self.events.append((hit[0], hit[1], line, hit[2]))

    def drain(self):
        with self.lock:
            out, self.events = self.events, []
        return out

    def stop(self, grace: float = 2.0):
        time.sleep(grace)                       # let the tail of the journal arrive
        if self.proc:
            if self.proc.poll() is None:
                try:
                    os.killpg(self.proc.pid, signal.SIGTERM)
                except OSError:
                    pass
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.wait()
            self.thread.join(timeout=2)
            if not self.thread.is_alive():
                self.proc.stdout.close()


class Runner:
    def __init__(self, args, rep: Reporter):
        self.a, self.rep = args, rep
        self.target = args.target
        self.image = Path(args.image).expanduser().resolve()
        self.oams = sorted(set(args.oams or ()))
        self.job = re.sub(r"[^A-Za-z0-9_.-]", "_", args.job_id or time.strftime("%Y%m%d-%H%M%S"))
        self.expected = self.sha = ""
        self.size = 0
        self.writing = False
        self.result: dict[int, dict] = {i: {"state": "pending"} for i in self.oams}
        signal.signal(signal.SIGTERM, self._on_term)

    def _on_term(self, *_):
        if self.writing:
            self.rep.say("cancel REFUSED: flash writes are in progress and cannot be aborted "
                         "safely; the job continues to verification")
            self.rep.emit("cancel", accepted=False)
            return
        self.rep.emit("cancel", accepted=True)
        raise Cancelled()

    def mark(self, i, state, msg="", **kw):
        self.result[i].update(state=state, **kw)
        if "reason" not in kw and state == "failed" and msg:
            self.result[i]["reason"] = msg
        self.rep.oam(i, state, msg, **{k: v for k, v in kw.items() if k in ("pct", "after", "before")})

    # ---- shared phases
    def check_image(self):
        self.rep.step("check", f"image {self.image}")
        if not self.image.is_file() or self.image.stat().st_size == 0:
            raise FlashError(f"image missing or empty: {self.image}")
        self.sha = sha256(self.image)
        if self.a.sha256 and self.sha != self.a.sha256.lower():
            raise FlashError(f"image SHA-256 {self.sha} != expected {self.a.sha256}")
        self.size = self.image.stat().st_size
        stamped = image_version(self.image) if self.a.method == "redfish" or not self.a.expect else ""
        if self.a.method == "redfish" and (not stamped or (self.a.expect and self.a.expect != stamped)):
            raise FlashError("Redfish requires an embedded build ID matching any supplied --expect")
        self.expected = self.a.expect or stamped
        self.rep.say(f"size {self.size} bytes, sha256 {self.sha}")
        if not self.expected:
            raise FlashError("no build ID (X.Y.Z-DDMMYYYY-HHMMSS) found in the image; "
                             "pass --expect with the build read-back must show")
        self.rep.say(f"expected build after flashing: {self.expected}")
        self.rep.emit("image", sha256=self.sha, size=self.size, expected=self.expected,
                      path=str(self.image))

    def preflight_bmc(self, required=True, info=None):
        self.rep.step("preflight", "BMC: device state and current build per OAM")
        if info is None:
            info = probe_bmc(self.target)
        if not info.get("ok"):
            if required:
                raise FlashError(f"BMC: {info.get('error')}")
            self.rep.say(f"no BMC read-back available: {info.get('error')}")
            return None
        known = {o["index"]: o for o in info["oams"]}
        for i in self.oams:
            o = known.get(i)
            if o is None:
                raise FlashError(f"OAM {i} is not reported by the BMC (known: {sorted(known)})")
            state = o.get("state", "")
            self.result[i]["before"] = o.get("build", "")
            self.mark(i, "ready", f"state {state or '?'}, build {o.get('build') or '?'}",
                      before=o.get("build", ""))
            if self.a.method in ("bmc", "redfish") and state == "INIT_FAILED":
                self.rep.say(f"OAM {i}: warning: INIT_FAILED (BMC init incomplete); "
                             "verifying the actual flash result", oam=i)
            elif state not in ("READY", "") and not self.a.force:
                raise FlashError(f"OAM {i} is {state}, not READY (flash-idle); "
                                 "re-run with --force to override")
        return info

    def stage_on(self, where: str, rdir: str) -> str:
        """Upload the image binary-safe over ssh stdin and verify its SHA-256 there."""
        q = shlex.quote
        rimg = f"{rdir}/{self.image.name}"
        self.rep.step("upload", f"{self.image.name} -> {where}:{rimg}")
        script = f"mkdir -p {q(rdir)} && cat > {q(rimg)} && sha256sum {q(rimg)}"
        if where == "bmc":
            rc, out, err = bmc_sh(self.target, script, stdin=self.image.read_bytes(), timeout=600)
        else:
            rc, out, err = host_sh(self.target, script, stdin=self.image.read_bytes(), timeout=600)
        got = out.split()[0] if out.split() else ""
        if rc != 0 or got != self.sha:
            raise FlashError(f"upload to the {where} failed verification "
                             f"(rc={rc}, sha256 {got or '?'}; {_last_error(err, rc)})")
        self.rep.say(f"staged on the {where}; sha256 matches")
        self.rep.emit("upload", pct=100)
        return rimg

    def live_fpt_builds(self, i: int) -> tuple[list[str], str]:
        rc, text, _ = bmc_sh(self.target, f"bmccli flash version -f -d {i} 2>&1", timeout=120)
        return (parse_dev_kv(text).get(i, {}).get("builds", []) if rc == 0 else []), text

    def readback_bmc(self, *, require_three=False) -> dict[int, list[str]]:
        out = {}
        for i in self.oams:
            if self.result[i]["state"] == "failed":
                continue
            text = ""
            for attempt in range(1, 11):
                builds, text = self.live_fpt_builds(i)
                if builds and (not require_three or len(builds) >= 3):
                    break
                self.rep.say(f"OAM {i}: live read-back not ready ({text.strip()[:120]}); "
                             f"retry {attempt}/10")
                if attempt < 10:
                    time.sleep(POLL * 2)
            out[i] = builds
            self.rep.say(f"OAM {i}: read-back (live, -f) builds {out[i] or '[none]'}")
        return out

    def verify_builds(self, builds: dict[int, list[str]]):
        self.rep.step("verify", f"read-back must show {self.expected} in every FPT copy")
        for i, b in builds.items():
            before = self.result[i].get("before", "")
            if not b or any(x != self.expected for x in b):
                self.mark(i, "failed", f"read-back {b or 'empty'} != {self.expected}",
                          after=",".join(b))
            elif self.result[i]["state"] in WRITTEN or (before and before != self.expected):
                note = (" (same build ID as before; the write itself is confirmed by the "
                        "tool/orchestrator)" if before == self.expected else "")
                self.mark(i, "verified", f"FPT build {b[0]}{note}", pct=100, after=b[0])
            else:
                self.mark(i, "unverified", f"read-back shows {b[0]}, but it was already "
                          f"{before or '?'} and no write completion was observed", after=b[0],
                          reason="no write evidence for an identical build")

    def boot_verify_bmc(self):
        good = [i for i in self.oams if self.result[i]["state"] == "verified"]
        if not good:
            return
        self.rep.step("boot", "reset the A1 and prove the new build runs (device fw-version)")
        for i in good:
            self.mark(i, "resetting", "bmccli device reset a1")
            rc, out, _ = bmc_sh(self.target, f"bmccli device reset a1 -d {i} 2>&1", timeout=300)
            for line in out.splitlines():
                self.rep.say(f"  bmccli: {line}", oam=i)
        deadline, pending = time.time() + 600, set(good)
        while pending and time.time() < deadline:
            time.sleep(POLL * 2)
            _, out, _ = bmc_sh(self.target, "bmccli device status -d all 2>&1; echo __FW__; "
                                            "bmccli device fw-version -d all 2>&1", timeout=120)
            st_text, _, fw_text = out.partition("__FW__")
            st, fw = parse_dev_kv(st_text), parse_dev_kv(fw_text)
            for i in sorted(pending):
                running = (fw.get(i, {}).get("values") or [""])[-1]
                if st.get(i, {}).get("state") == "READY" and running:
                    pending.discard(i)
                    if same_build(running, self.expected):
                        self.result[i]["booted"] = running
                        self.mark(i, "verified", f"booted, running {running}", pct=100)
                    else:
                        self.mark(i, "failed", f"running {running} after reset, not {self.expected}")
        for i in pending:
            self.mark(i, "failed", "did not return to READY within 600s after reset")

    # ---- method: redfish (A1 whole tray)
    def preflight_redfish(self, client: Redfish):
        info = probe_bmc(self.target)
        if not info.get("ok"):
            raise FlashError(f"BMC: {info.get('error')}")
        shell_slots = {o["index"] for o in info["oams"] if o.get("state")}
        slots = client.slots()
        if shell_slots != slots or len(slots) != self.a.expect_oams:
            raise FlashError(f"whole-tray inventory mismatch: expected {self.a.expect_oams}, "
                             f"Redfish {sorted(slots)}, BMC shell {sorted(shell_slots)}")
        self.oams = sorted(slots)
        self.result = {i: {"state": "pending"} for i in self.oams}
        self.preflight_bmc(info=info)

    def wait_redfish(self, client: Redfish, tid: str | None, before: set[str], deadline: float):
        discover_until = min(deadline, time.monotonic() + 10)
        while tid is None and (remaining := discover_until - time.monotonic()) > 0:
            candidates, unresolved = [], False
            for i in client.task_ids(timeout=remaining) - before:
                remaining = discover_until - time.monotonic()
                if remaining <= 0:
                    unresolved = True
                    break
                try:
                    if client.task(i, timeout=remaining).get("Name") == "UpdateA1":
                        candidates.append(i)
                except RedfishHTTPError as e:
                    if e.status != 404:
                        raise
                    unresolved = True
            if len(candidates) > 1:
                raise FlashError("multiple new UpdateA1 tasks; cannot identify this update")
            if candidates and not unresolved:
                tid = candidates[0]
            else:
                time.sleep(min(POLL, max(0, discover_until - time.monotonic())))
        if tid is None:
            raise FlashError("UpdateA1 accepted but its task could not be identified")
        last, missing_since = None, None
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                task = client.task(tid, timeout=min(30, remaining))
            except RedfishHTTPError as e:
                if e.status != 404:
                    raise
                if missing_since is None:
                    missing_since = time.monotonic()
                if time.monotonic() - missing_since >= 30:
                    raise
            else:
                missing_since = None
                if task.get("Name") not in (None, "UpdateA1"):
                    raise FlashError(f"Redfish task {tid} is not UpdateA1")
                state, pct, status = (task.get(k) for k in ("TaskState", "PercentComplete", "TaskStatus"))
                if (state, pct, status) != last:
                    self.rep.say(f"Redfish task {tid}: {state}, progress {pct}, status {status}")
                    last = state, pct, status
                if isinstance(state, str) and state in REDFISH_TERMINAL:
                    return tid, state, status
            time.sleep(min(POLL, max(0, deadline - time.monotonic())))
        raise FlashError(f"Redfish UpdateA1 task {tid} did not finish within {self.a.timeout}s")

    def verify_redfish(self, prior, journal):
        self.rep.step("verify", "all three live FPT copies on every OAM must match the image")
        completed, failures = set(), {}
        for i, kind, line, _ in journal:
            if kind == "fail":
                failures[i] = line[-200:]
            elif kind == "done" and REDFISH_WRITTEN.search(line) and not re.search(
                    r"\b(?:skip(?:ped)?|no[- ]op|no (?:changes?|updates?|writes?)"
                    r"(?: needed| required)?)\b", line, re.I):
                completed.add(i)
        expected = [self.expected] * 3
        for i, builds in self.readback_bmc(require_three=True).items():
            if i in failures:
                self.mark(i, "failed", failures[i], after=",".join(builds))
            elif builds != expected:
                self.mark(i, "failed", f"three matching FPT copies required; got {builds}",
                          after=",".join(builds))
            elif i in completed or (len(prior[i]) == 3 and len(set(prior[i])) == 1
                                     and prior[i] != expected):
                self.mark(i, "written", f"three FPT copies match {self.expected}; boot not verified",
                          pct=100, after=self.expected, reason="boot not verified")
            else:
                self.mark(i, "unverified", "FPTs match, but no per-OAM write evidence was observed",
                          after=self.expected, reason="no per-OAM write evidence")

    def run_redfish(self):
        host = redfish_host(self.target)
        client = Redfish(host, self.a.redfish_ca)
        self.preflight_redfish(client)
        self.rep.say(f"Redfish {host}: whole-tray scope, OAMs {self.oams}")
        client.idle_tasks()
        rc, _, err = bmc_sh(self.target, f"mkdir -m 700 {REDFISH_LOCK}", timeout=30)
        if rc:
            raise FlashError(f"cannot acquire Redfish staging lock {REDFISH_LOCK} "
                             f"({_last_error(err, rc)})")
        uncertain = False
        try:
            client.idle_tasks()
            prior = {i: self.live_fpt_builds(i)[0] for i in self.oams}
            for i, builds in prior.items():
                consistent = len(builds) == 3 and len(set(builds)) == 1
                self.result[i]["before"] = builds[0] if consistent else ""
            self.rep.step("upload", f"{self.image.name} -> {host}:{REDFISH_IMAGE}")
            try:
                with self.image.open("rb") as image:
                    uncertain = True
                    client.request("POST", REDFISH_UPLOAD, body=image, timeout=600, headers={
                        "Content-Type": "application/octet-stream", "Content-Length": str(self.size)})
                    uncertain = False
            except OSError as e:
                raise FlashError(f"cannot open or read image {self.image}: {e}") from e
            follower = JournalFollower(self.target, self.rep)
            follower.start()
            try:
                before = client.idle_tasks()
                if redfish_host(self.target).lower() != host.lower():
                    raise FlashError("the mctl BMC registration changed during staging")
                rc, out, err = bmc_sh(self.target, f"sha256sum {REDFISH_IMAGE}", timeout=120)
                tokens = out.split()
                got = tokens[0] if tokens else ""
                if rc:
                    raise FlashError(f"cannot read Redfish staged image SHA-256 ({_last_error(err, rc)})")
                if got != self.sha:
                    raise FlashError(f"Redfish staged image SHA-256 {got or '?'} != {self.sha}")
                self.rep.emit("upload", pct=100)
                follower.drain()
                self.writing = uncertain = True
                self.rep.step("write", "Redfish UpdateA1 (all OAMs)")
                deadline = time.monotonic() + self.a.timeout
                body, location = client.request("POST", REDFISH_UPDATE, body=b"{}", headers={
                    "Content-Type": "application/json"}, timeout=min(30, self.a.timeout))
                for i in self.oams:
                    self.mark(i, "queued", "Redfish accepted the whole-tray update", pct=0)
                tid, state, status = self.wait_redfish(
                    client, client.returned_task(body, location, before), before, deadline)
                uncertain = False
                if state != "Completed" or status not in (None, "OK", "Warning"):
                    raise FlashError(f"Redfish task {tid} failed: {state}, status {status}")
            finally:
                follower.stop()
            self.verify_redfish(prior, follower.drain())
        finally:
            try:
                if redfish_host(self.target).lower() != host.lower():
                    raise FlashError(f"the mctl BMC registration changed; inspect {REDFISH_LOCK} "
                                     f"on {host} and verify the original tray")
                if uncertain:
                    self.rep.say(f"Redfish outcome uncertain; retained {REDFISH_LOCK}. "
                                 "Check the task queue and orchestrator journal before retrying.")
                else:
                    rc, _, err = bmc_sh(self.target, f"rmdir {REDFISH_LOCK}", timeout=30)
                    if rc:
                        self.rep.say(f"could not release {REDFISH_LOCK}: {_last_error(err, rc)}")
            finally:
                self.writing = False

    # ---- method: bmc
    def run_bmc(self):
        self.preflight_bmc()
        rdir = f"/tmp/fw-flash/{self.job}"
        rimg = self.stage_on("bmc", rdir)
        follower = JournalFollower(self.target, self.rep)
        follower.start()
        try:
            time.sleep(min(POLL, 1.5))
            self.rep.step("write", "bmccli flash update (queued; the orchestrator programs the NOR)")
            self.writing = True
            for i in self.oams:
                rc, out, err = bmc_sh(self.target,
                                      f"bmccli flash update {shlex.quote(rimg)} -d {i} 2>&1",
                                      timeout=120)
                for line in out.splitlines():
                    self.rep.say(f"  bmccli: {line}", oam=i)
                if rc != 0:
                    self.mark(i, "failed", f"bmccli refused the update (rc={rc})")
                else:
                    self.mark(i, "queued", "accepted by the orchestrator", pct=0)
            self.monitor_bmc(follower)
        finally:
            self.writing = False
            follower.stop()
        self.verify_builds(self.readback_bmc())
        if self.a.reset_verify:
            self.boot_verify_bmc()
        bmc_sh(self.target, f"rm -rf {shlex.quote(rdir)}", timeout=60)

    def monitor_bmc(self, follower: JournalFollower):
        """Wait for every queued OAM to settle. The orchestrator journal decides the
        outcome; a device that left READY and came back also counts as written.
        INIT_FAILED alone is neither a write failure nor a write transition."""
        active = {i for i in self.oams if self.result[i]["state"] == "queued"}
        left_ready: set[int] = set()
        last: dict[int, str] = {}
        t0 = time.time()
        while active and time.time() - t0 < self.a.timeout:
            time.sleep(POLL)
            for i, kind, line, pct in follower.drain():
                if i not in active:
                    continue
                if kind == "fail":
                    self.mark(i, "failed", line[-200:])
                    active.discard(i)
                elif kind == "done":
                    self.mark(i, "written", "orchestrator reported completion", pct=100)
                    active.discard(i)
                else:
                    self.mark(i, "writing", "" if self.result[i]["state"] == "writing"
                              else "orchestrator started", **({"pct": pct} if pct is not None else {}))
            rc, out, _ = bmc_sh(self.target, "bmccli device status -d all 2>&1", timeout=60)
            st = parse_dev_kv(out)
            for i in sorted(active):
                state = st.get(i, {}).get("state", "")
                if not state:
                    continue
                if state != last.get(i):
                    last[i] = state
                    self.rep.say(f"OAM {i}: device state {state}", oam=i)
                if state == "INIT_FAILED":
                    continue
                if state != "READY":
                    left_ready.add(i)
                    if self.result[i]["state"] == "queued":
                        self.mark(i, "writing", f"device {state}")
                    if re.search(r"FAIL|ERROR|FATAL", state):
                        self.mark(i, "failed", f"device state {state}")
                        active.discard(i)
                elif i in left_ready:
                    self.mark(i, "written", "device back to READY", pct=100)
                    active.discard(i)
        for i in active:
            self.rep.say(f"OAM {i}: no completion seen within {self.a.timeout}s; read-back decides",
                         oam=i)

    # ---- method: pcie / spi-ftdi (fw_ctl on the host)
    def run_fw_ctl(self):
        transport = "pcie" if self.a.method == "pcie" else "ftdi-spi"
        host = probe_host(self.target)
        why = host_reason(host, self.a.method)
        if why:
            raise FlashError(why)
        bmc = self.preflight_bmc(required=False)
        rdir = f".mctl/flash/{self.job}"
        self.stage_on("host", rdir)
        sudo = "" if host["root"] else 'sudo -n -E env PATH="$PATH" '
        sel = " ".join(str(i) for i in self.oams)
        fw_ctl = (f"cd {FW_CTL_DIR} && . {FW_CTL_SETUP} asic >/dev/null 2>&1; "
                  f"{sudo}python3 -m fw_ctl.cli --variant asic --interface pcie ")
        cmd = (fw_ctl + f'flash write "$HOME/{rdir}/{self.image.name}" --transport {transport} '
               f"--oam {sel}{' --force' if self.a.force else ''}")
        self.rep.step("write", f"fw_ctl flash write --transport {transport} --oam {sel}")
        self.writing = True
        try:
            rc = self.remote_detached(host["dest"], rdir, cmd)
        finally:
            self.writing = False
        for i in self.oams:
            if rc != 0:
                self.mark(i, "failed", f"fw_ctl flash write rc={rc}")
            else:
                self.mark(i, "written", "fw_ctl reported success", pct=100)
        if bmc:
            self.verify_builds(self.readback_bmc())
            if self.a.reset_verify:
                self.boot_verify_bmc()
        else:
            self.rep.step("verify", "no BMC: fw_ctl host_lib rom-status version")
            _, out, _ = host_sh(self.target, fw_ctl + "host_lib rom-status version 2>&1",
                                dest=host["dest"], timeout=300)
            seen = set(VERSION_RE.findall(out))
            for i in self.oams:
                if self.result[i]["state"] == "failed":
                    continue
                if self.expected in seen and len(self.oams) == 1:
                    self.mark(i, "verified", f"rom-status shows {self.expected}", pct=100)
                else:
                    self.mark(i, "unverified", "no per-OAM read-back on a machine without a BMC",
                              reason="no per-OAM read-back")
        host_sh(self.target, f'rm -rf "$HOME/{rdir}"', dest=host["dest"], timeout=60)

    def remote_detached(self, dest: str, rdir: str, cmd: str) -> int:
        """Run ``cmd`` detached on the host (survives a dropped ssh) and stream its log."""
        log, done = f'"$HOME/{rdir}/write.log"', f'"$HOME/{rdir}/write.rc"'
        inner = f"{cmd}; echo $? > {done}"
        launch = (f"setsid nohup bash -c {shlex.quote(inner)} > {log} 2>&1 < /dev/null & "
                  "echo __LAUNCHED__")
        rc, out, err = host_sh(self.target, launch, dest=dest, timeout=60)
        if "__LAUNCHED__" not in out:
            raise FlashError(f"could not start fw_ctl on the host: {_last_error(err, rc)}")
        seen, t0 = 0, time.time()
        while time.time() - t0 < self.a.timeout:
            time.sleep(POLL)
            _, out, _ = host_sh(self.target, f"tail -n +{seen + 1} {log} 2>/dev/null; "
                                             f"echo __RC__; cat {done} 2>/dev/null", dest=dest, timeout=60)
            body, _, tail = out.partition("__RC__")
            for line in body.splitlines():
                seen += 1
                self.rep.say(f"  fw_ctl: {line}")
                m = PCT_RE.search(line)
                if m:
                    for i in self.oams:
                        self.rep.oam(i, "writing", pct=min(100, int(float(m.group(1)))))
            if tail.strip():
                return int(tail.strip()) if tail.strip().isdigit() else 1
        raise FlashError(f"fw_ctl did not finish within {self.a.timeout}s (still running on the host)")

    # ---- method: spi-bmc (fwloader MTD on the BMC)
    def run_spi_bmc(self):
        info = probe_bmc(self.target)
        why = bmc_mtd_reason(info)
        if why:
            raise FlashError(f"spi-bmc unavailable: {why}")
        fwl, q = info["fwloader"], shlex.quote
        rdir = f"/tmp/fw-flash/{self.job}"
        rimg = self.stage_on("bmc", rdir)
        i = self.oams[0]
        self.rep.step("write", f"fwloader -m flash -d {BMC_MTD}")
        self.writing = True
        rc = 1
        try:
            proc = subprocess.Popen([mctl(), "-t", self.target, "bmc",
                                     f"{q(fwl)} -m flash -i {q(rimg)} -d {BMC_MTD}"
                                     f"{' -f' if self.a.force else ''} 2>&1; echo __RC__=$?"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                line = line.rstrip()
                m = re.fullmatch(r"__RC__=(\d+)", line)
                if m:
                    rc = int(m.group(1))
                    continue
                self.rep.say(f"  fwloader: {line}")
                p = PCT_RE.search(line)
                if p:
                    self.rep.oam(i, "writing", pct=min(100, int(float(p.group(1)))))
            proc.wait()
        finally:
            self.writing = False
        if rc != 0:
            self.mark(i, "failed", f"fwloader rc={rc}")
        else:
            self.rep.step("verify", "dump the flash and compare SHA-256 over the image length")
            _, out, _ = bmc_sh(self.target, f"{q(fwl)} -m flash -o {q(rdir)}/dump.bin -d {BMC_MTD} "
                                            f">/dev/null 2>&1 && head -c {self.size} {q(rdir)}/dump.bin "
                                            "| sha256sum", timeout=900)
            got = out.split()[0] if out.split() else ""
            if got == self.sha:
                self.mark(i, "verified", "flash dump matches the image byte-for-byte", pct=100,
                          after=self.expected)
            else:
                self.mark(i, "failed", f"flash dump sha256 {got or '?'} != image")
        bmc_sh(self.target, f"rm -rf {q(rdir)}", timeout=60)

    def run(self) -> int:
        self.rep.emit("start", target=self.target, method=self.a.method, oams=self.oams,
                      image=str(self.image), force=self.a.force, reset_verify=self.a.reset_verify,
                      **({"all_oams": True} if self.a.method == "redfish" else {}))
        scope = "all OAMs" if self.a.method == "redfish" else f"OAMs {self.oams}"
        self.rep.say(f"flash {self.image.name} -> {self.target} via {METHOD_LABELS[self.a.method]}, "
                     f"{scope}")
        try:
            self.check_image()
            {"bmc": self.run_bmc, "pcie": self.run_fw_ctl, "spi-ftdi": self.run_fw_ctl,
             "spi-bmc": self.run_spi_bmc, "redfish": self.run_redfish}[self.a.method]()
        except Cancelled:
            self.rep.step("cancelled", "stopped before any flash write")
            for i in self.oams:
                self.result[i]["state"] = "cancelled"
            self.rep.emit("result", ok=False, cancelled=True, oams=self.result)
            return 130
        except FlashError as e:
            self.rep.step("error", str(e))
            for i in self.oams:
                if self.result[i]["state"] not in ("verified", "failed", "unverified"):
                    self.result[i].update(state="failed", reason=str(e))
            self.rep.emit("result", ok=False, error=str(e), oams=self.result)
            return 1
        ok = bool(self.result) and all(r["state"] == "verified" for r in self.result.values())
        summary = ", ".join(f"OAM {i}: {r['state']}" for i, r in self.result.items())
        deferred = self.a.method == "redfish" and all(r["state"] == "written"
                                                     for r in self.result.values())
        outcome = ("ALL VERIFIED" if ok else
                   "WRITTEN, BOOT NOT VERIFIED" if deferred else "NOT VERIFIED")
        self.rep.step("done", f"{outcome}: {summary}")
        self.rep.emit("result", ok=ok, oams=self.result, expected=self.expected)
        return 0 if ok else 2


def parser():
    p = argparse.ArgumentParser(prog="fw-flash", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    d = sub.add_parser("discover", help="read-only: methods, OAMs and their current builds")
    d.add_argument("--target", required=True)
    d.add_argument("--json", action="store_true")
    r = sub.add_parser("run", help="flash + read-back verify (+ optional reset/boot verify)")
    r.add_argument("--target", required=True)
    r.add_argument("--method", required=True, choices=CLI_METHODS)
    r.add_argument("--image", required=True)
    scope = r.add_mutually_exclusive_group(required=True)
    scope.add_argument("--oam", dest="oams", type=int, action="append",
                       help="0-based OAM index; repeat for several (not Redfish)")
    scope.add_argument("--all-oams", action="store_true", help="entire tray (Redfish only)")
    r.add_argument("--expect-oams", type=int, help="required tray OAM count for Redfish")
    r.add_argument("--redfish-ca", help="PEM trust file for Redfish TLS (default: system trust)")
    r.add_argument("--sha256", help="refuse unless the image hashes to this")
    r.add_argument("--expect", help="build ID read-back must show (default: read from the image)")
    r.add_argument("--force", action="store_true",
                   help="override blocking OAM state checks and force direct flash writes; "
                        "not needed for INIT_FAILED with --method bmc")
    r.add_argument("--reset-verify", action="store_true",
                   help="reset each OAM and check the new build (not supported for Redfish)")
    r.add_argument("--events", help="append JSON-lines progress events to this file")
    r.add_argument("--job-id", help="name of the remote staging directory")
    r.add_argument("--timeout", type=int, default=TIMEOUT)
    return p


def main(argv=None):
    p = parser()
    a = p.parse_args(argv)
    if a.command == "discover":
        res = discover(a.target)
        if a.json:
            print(json.dumps(res, indent=2))
            return 0
        for m, v in res["methods"].items():
            print(f"{m:9} {'yes' if v['available'] else 'no ':3} {v.get('reason', '')}")
            for o in v.get("oams") or []:
                print(f"          {o['label']:12} {o.get('state', ''):10} {o.get('build', '')}")
        return 0
    if a.method == "redfish":
        if not a.all_oams or not a.expect_oams or a.expect_oams < 1:
            p.error("redfish requires --all-oams and a positive --expect-oams")
        if not a.sha256 or not re.fullmatch(r"[a-fA-F0-9]{64}", a.sha256):
            p.error("redfish requires --sha256 with the expected 64-digit image SHA-256")
        if a.force or a.reset_verify or a.timeout <= 0:
            p.error("redfish requires a positive --timeout and does not support --force or "
                    "--reset-verify (verify boot separately)")
    elif a.all_oams or a.expect_oams is not None or a.redfish_ca is not None:
        p.error("--all-oams, --expect-oams and --redfish-ca are only for redfish")
    if a.method == "spi-bmc" and len(set(a.oams)) != 1:
        print("fw-flash: spi-bmc writes the single soc-nv-flash MTD; pass exactly one --oam",
              file=sys.stderr)
        return 2
    runner = Runner(a, Reporter(a.events))
    if a.method != "redfish":
        return runner.run()
    previous = signal.signal(signal.SIGINT, runner._on_term)
    try:
        return runner.run()
    finally:
        signal.signal(signal.SIGINT, previous)


if __name__ == "__main__":
    sys.exit(main())
