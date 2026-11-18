#!/usr/bin/env python3
"""fw-flash: discover flash paths on a lab machine and flash an image onto its OAMs.

Every machine is an mctl target (``mctl -t T dest`` for the host, ``mctl -t T bmc``
for its BMC). Four delivery methods, each verified by READ-BACK, never by exit code:

* ``bmc``      ``bmccli flash update`` on the tray BMC (queued in the orchestrator
               daemon); progress is the daemon's ``journalctl -u orchestrator``;
               read-back ``bmccli flash version -f`` (live FPT header re-read).
* ``pcie``     ``fw_ctl flash write --transport pcie`` on the host (root, no el_*
               driver bound); read-back through the BMC when the tray has one.
* ``spi-ftdi`` ``fw_ctl flash write --transport ftdi-spi`` on the host.
* ``spi-bmc``  ``fwloader -m flash -d /dev/mtd/soc-nv-flash`` on the BMC; read-back
               is a flash dump compared (SHA-256 over the image length) with the image.

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
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

METHODS = ("bmc", "pcie", "spi-ftdi", "spi-bmc")
METHOD_LABELS = {"bmc": "BMC (bmccli flash update)", "pcie": "PCIe (fw_ctl)",
                 "spi-ftdi": "SPI over FTDI (fw_ctl)", "spi-bmc": "SPI from the BMC (fwloader MTD)"}
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


class FlashError(Exception):
    pass


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
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class Runner:
    def __init__(self, args, rep: Reporter):
        self.a, self.rep = args, rep
        self.target = args.target
        self.image = Path(args.image).expanduser().resolve()
        self.oams = sorted(set(args.oams))
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
        self.expected = self.a.expect or image_version(self.image)
        self.rep.say(f"size {self.size} bytes, sha256 {self.sha}")
        if not self.expected:
            raise FlashError("no build ID (X.Y.Z-DDMMYYYY-HHMMSS) found in the image; "
                             "pass --expect with the build read-back must show")
        self.rep.say(f"expected build after flashing: {self.expected}")
        self.rep.emit("image", sha256=self.sha, size=self.size, expected=self.expected,
                      path=str(self.image))

    def preflight_bmc(self, required=True):
        self.rep.step("preflight", "BMC: device state and current build per OAM")
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
            self.result[i]["before"] = o.get("build", "")
            self.mark(i, "ready", f"state {o.get('state') or '?'}, build {o.get('build') or '?'}",
                      before=o.get("build", ""))
            if o.get("state") not in ("READY", "") and not self.a.force:
                raise FlashError(f"OAM {i} is {o.get('state')}, not READY (flash-idle); "
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

    def readback_bmc(self) -> dict[int, list[str]]:
        out = {}
        for i in self.oams:
            if self.result[i]["state"] == "failed":
                continue
            text = ""
            for attempt in range(1, 11):
                rc, text, _ = bmc_sh(self.target, f"bmccli flash version -f -d {i} 2>&1", timeout=120)
                if rc == 0 and "build=" in text:
                    break
                self.rep.say(f"OAM {i}: live read-back not ready ({text.strip()[:120]}); "
                             f"retry {attempt}/10")
                time.sleep(POLL * 2)
            out[i] = parse_dev_kv(text).get(i, {}).get("builds", [])
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
        outcome; a device that left READY and came back also counts as written."""
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
                      image=str(self.image), force=self.a.force, reset_verify=self.a.reset_verify)
        self.rep.say(f"flash {self.image.name} -> {self.target} via {METHOD_LABELS[self.a.method]}, "
                     f"OAMs {self.oams}")
        try:
            self.check_image()
            {"bmc": self.run_bmc, "pcie": self.run_fw_ctl, "spi-ftdi": self.run_fw_ctl,
             "spi-bmc": self.run_spi_bmc}[self.a.method]()
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
        ok = all(r["state"] == "verified" for r in self.result.values())
        summary = ", ".join(f"OAM {i}: {r['state']}" for i, r in self.result.items())
        self.rep.step("done", ("ALL VERIFIED: " if ok else "NOT VERIFIED: ") + summary)
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
    r.add_argument("--method", required=True, choices=METHODS)
    r.add_argument("--image", required=True)
    r.add_argument("--oam", dest="oams", type=int, action="append", required=True,
                   help="0-based OAM index; repeat for several")
    r.add_argument("--sha256", help="refuse unless the image hashes to this")
    r.add_argument("--expect", help="build ID read-back must show (default: read from the image)")
    r.add_argument("--force", action="store_true",
                   help="flash a non-READY OAM and pass --force/-f to the write tool")
    r.add_argument("--reset-verify", action="store_true",
                   help="after read-back, reset each OAM and prove the new build runs")
    r.add_argument("--events", help="append JSON-lines progress events to this file")
    r.add_argument("--job-id", help="name of the remote staging directory")
    r.add_argument("--timeout", type=int, default=TIMEOUT)
    return p


def main(argv=None):
    a = parser().parse_args(argv)
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
    if a.method == "spi-bmc" and len(set(a.oams)) != 1:
        print("fw-flash: spi-bmc writes the single soc-nv-flash MTD; pass exactly one --oam",
              file=sys.stderr)
        return 2
    return Runner(a, Reporter(a.events)).run()


if __name__ == "__main__":
    sys.exit(main())
