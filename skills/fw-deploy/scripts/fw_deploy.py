#!/usr/bin/env python3
"""Local-first firmware artifact transfers. Never builds or programs devices."""

import argparse
import base64
import binascii
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile


FILES = ("CHANGE_NOTES.md", "deployment.json")
COMPONENT = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
DEPLOYMENT = re.compile(r"\d{8}_\d{6}_[a-z0-9][a-z0-9_-]*\Z")


class DeployError(Exception):
    pass


def component(value):
    if not COMPONENT.fullmatch(value):
        raise argparse.ArgumentTypeError("use lowercase letters, digits, '_' or '-'")
    return value


def regular(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise DeployError(f"not a regular, non-symlink file: {path}")
    return path


def identity(path):
    path = regular(path)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    info = path.stat()
    return {"size": info.st_size, "sha256": digest.hexdigest(),
            "mtime_ns": info.st_mtime_ns}


def run(argv):
    result = subprocess.run(argv, capture_output=True, text=True, check=False,
                            env={**os.environ, "MCTL_AUTOSTART": "0"})
    if result.returncode:
        raise DeployError(
            f"{shlex.join(argv[:4])} failed ({result.returncode}): "
            f"{result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def remote(args, code, values=(), fallback=True):
    if not args.workspace:
        raise DeployError("--workspace is required for mctl remote operations")
    command = shlex.join(["python3", "-c", code, *map(str, values)])
    if fallback:
        command = ': || cd "$HOME"; set -e; ' + command
    return ["mctl", "-t", args.target, "sh", command,
            str(Path(args.workspace).expanduser().resolve())]


REMOTE_IDENTITY = """
import hashlib, json, os, stat, sys
p = os.path.expanduser(sys.argv[1])
s = os.lstat(p)
if not stat.S_ISREG(s.st_mode):
    raise SystemExit("source is not a regular file")
h = hashlib.sha256()
with open(p, "rb") as f:
    for b in iter(lambda: f.read(1048576), b""):
        h.update(b)
print(json.dumps(dict(size=s.st_size, sha256=h.hexdigest(), mtime_ns=s.st_mtime_ns)))
"""

REMOTE_PATH = """
import os, subprocess, sys
p = os.path.expanduser(sys.argv[1])
if not os.path.isabs(p):
    raise SystemExit("destination must be absolute")
p = os.path.realpath(p)
if p == "/" or p == "/dev" or p.startswith("/dev/"):
    raise SystemExit("destination must be an absolute directory, not root or a device")
if sys.argv[2] == "nfs":
    existing = p
    while not os.path.exists(existing):
        existing = os.path.dirname(existing)
    fs = subprocess.check_output(
        ["findmnt", "-n", "-o", "FSTYPE", "-T", existing], text=True).strip()
    if fs not in ("nfs", "nfs4"):
        raise SystemExit("mirror root is not on NFS: " + fs)
print(p)
"""

REMOTE_CHECK = """
import hashlib, json, os, stat, sys
directory, expected, create = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3] == "create"
if create:
    os.makedirs(directory, exist_ok=True)
if not os.path.isdir(directory):
    raise SystemExit("destination directory is absent")
for name, wanted in expected.items():
    p = os.path.join(directory, name)
    if not os.path.lexists(p):
        if create:
            continue
        raise SystemExit("missing file: " + p)
    s = os.lstat(p)
    if not stat.S_ISREG(s.st_mode):
        raise SystemExit("not a regular file: " + p)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1048576), b""):
            h.update(b)
    if s.st_size != wanted["size"] or h.hexdigest() != wanted["sha256"]:
        raise SystemExit("checksum conflict: " + p)
print("verified")
"""


def retrieve(args, output):
    expected = json.loads(run(remote(args, REMOTE_IDENTITY, [args.source], False)))
    code = (
        "import base64, os, sys; "
        "f=open(os.path.expanduser(sys.argv[1]), 'rb'); "
        "base64.encode(f, sys.stdout.buffer)"
    )
    command = remote(args, code, [args.source], False)
    with output.open("xb") as stream:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE,
            env={**os.environ, "MCTL_AUTOSTART": "0"})
        try:
            for line in process.stdout:
                stream.write(base64.b64decode(line.strip(), validate=True))
            if process.wait():
                raise DeployError("remote image retrieval failed")
        except BaseException:
            if process.poll() is None:
                process.terminate()
            process.wait()
            raise
        finally:
            process.stdout.close()
    verify_file(output, expected)
    os.utime(output, ns=(expected["mtime_ns"], expected["mtime_ns"]))
    return expected


def verify_file(path, expected):
    actual = identity(path)
    if any(actual[key] != expected[key] for key in ("size", "sha256")):
        raise DeployError(f"checksum/size mismatch: {path}")


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".fw-deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def import_image(args):
    notes = regular(Path(args.notes).expanduser()).read_text()
    if not notes.strip():
        raise DeployError("change notes must not be empty")
    filename = Path(args.source).name
    safe_filename(filename)
    root = Path(args.root).expanduser().resolve()
    now = datetime.now(timezone.utc)
    name = now.strftime("%d%m%Y_%H%M%S") + "_" + args.topic
    project = root / args.project
    with locked(root):
        if project.is_symlink():
            raise DeployError(f"project directory must not be a symlink: {project}")
        project.mkdir(exist_ok=True)
        destination = project / name
        if destination.exists() or destination.is_symlink():
            raise DeployError(f"deployment already exists; retry with a fresh timestamp: {destination}")
        with tempfile.TemporaryDirectory(prefix=".import-", dir=project) as temp:
            staging = Path(temp)
            leaf = staging / args.variant if args.variant else staging
            leaf.mkdir(exist_ok=True)
            image = leaf / filename
            if args.target:
                source = retrieve(args, image)
            else:
                original = Path(args.source).expanduser()
                source = identity(original)
                shutil.copy2(original, image)
                verify_file(image, source)
            if source["size"] == 0:
                raise DeployError("refusing an empty firmware image")
            metadata = {
                "schema": 1, "project": args.project, "deployment": name,
                "created_utc": now.isoformat(), "platform": args.platform,
                "variant": args.variant or "unspecified", "image": filename,
                "source": source,
            }
            header = (
                f"# Deployment {name}\n\n"
                f"- UTC: {metadata['created_utc']}\n"
                f"- Project: {args.project}\n- Platform: {args.platform}\n"
                f"- Variant/revision/flavour: {metadata['variant']}\n"
                f"- Image: {filename}\n- Size: {source['size']} bytes\n"
                f"- SHA-256: {source['sha256']}\n"
                f"- Source mtime (ns since epoch): {source['mtime_ns']}\n\n"
                "Local copy verified against source SHA-256 and size. "
                "This is not hardware qualification; no flash/reset performed.\n\n"
            )
            (leaf / FILES[0]).write_text(header + notes.rstrip() + "\n")
            metadata["notes"] = identity(leaf / FILES[0])
            (leaf / FILES[1]).write_text(json.dumps(metadata, indent=2) + "\n")
            bundle(leaf)
            os.rename(staging, destination)
    final = destination / args.variant if args.variant else destination
    print(final)


def safe_filename(name):
    if (not name or name in (".", "..", *FILES) or Path(name).name != name
            or any(ord(char) < 32 for char in name)):
        raise DeployError(f"invalid image filename: {name!r}")


def bundle(directory):
    directory = Path(directory).expanduser().resolve()
    data = json.loads(regular(directory / FILES[1]).read_text())
    if data["schema"] != 1:
        raise DeployError("unsupported deployment metadata schema")
    safe_filename(data["image"])
    verify_file(directory / data["image"], data["source"])
    verify_file(directory / FILES[0], data["notes"])
    files = [directory / data["image"], *(directory / name for name in FILES)]
    return directory, data, {path.name: identity(path) for path in files}


def checked_destination(path):
    destination = Path(path).expanduser()
    if not destination.is_absolute():
        raise DeployError("destination must be absolute, not root or a device")
    destination = destination.resolve()
    if (destination == Path("/")
            or destination == Path("/dev") or Path("/dev") in destination.parents):
        raise DeployError("destination must be absolute, not root or a device")
    return destination


def copy_local(directory, expected, destination):
    destination = checked_destination(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name, wanted in expected.items():
        path = destination / name
        if path.exists() or path.is_symlink():
            verify_file(path, wanted)
    for name in expected:
        path = destination / name
        if path.exists():
            continue
        try:
            with (directory / name).open("rb") as source, path.open("xb") as output:
                shutil.copyfileobj(source, output)
            shutil.copystat(directory / name, path)
        except FileExistsError:
            verify_file(path, expected[name])
    for name, wanted in expected.items():
        verify_file(destination / name, wanted)


def destination_path(args, data, directory, mirror):
    raw = args.destination
    if args.target:
        raw = run(remote(args, REMOTE_PATH, [raw, "nfs" if mirror else "any"]))
    else:
        path = checked_destination(raw)
        if mirror:
            existing = path
            while not existing.exists():
                existing = existing.parent
            fs = run(["findmnt", "-n", "-o", "FSTYPE", "-T", str(existing)])
            if fs not in ("nfs", "nfs4"):
                raise DeployError(f"mirror root is not on NFS: {fs}")
        raw = str(path)
    if mirror:
        component(data["project"])
        if not DEPLOYMENT.fullmatch(data["deployment"]):
            raise DeployError("invalid deployment name in metadata")
        suffix = Path(data["project"]) / data["deployment"]
        if directory.name != data["deployment"]:
            suffix /= component(data["variant"])
        raw = str(Path(raw) / suffix)
    return raw


def copy_image(args, mirror=False, verify=False):
    directory, data, expected = bundle(args.directory)
    destination = destination_path(args, data, directory, mirror)
    if args.target:
        payload = json.dumps(expected)
        run(remote(args, REMOTE_CHECK,
                   [destination, payload, "check" if verify else "create"]))
        if not verify:
            host = run(["mctl", "-t", args.target, "dest"])
            if not host or host.startswith("-") or any(c.isspace() for c in host):
                raise DeployError("mctl returned an invalid SSH destination")
            run(["rsync", "-az", "--no-owner", "--no-group", "--checksum",
                 "--ignore-existing", "--protect-args", "-e",
                 "ssh -A -o BatchMode=yes -o ConnectTimeout=10",
                 "--", *(str(directory / name) for name in expected),
                 f"{host}:{destination}/"])
            run(remote(args, REMOTE_CHECK, [destination, payload, "check"]))
    elif verify:
        for name, wanted in expected.items():
            verify_file(Path(destination) / name, wanted)
    else:
        copy_local(directory, expected, destination)
    print(f"verified {args.target + ':' if args.target else ''}{destination}")


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", default="~/flash_images", help="canonical local image store")
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("import", "pull"):
        command = commands.add_parser(name, help="save a verified image in the local store")
        command.add_argument("source")
        command.add_argument("--project", required=True, type=component)
        command.add_argument("--topic", required=True, type=component)
        command.add_argument("--notes", required=True, help="baseline-relative change notes file")
        command.add_argument("--platform", required=True, type=component)
        command.add_argument("--variant", type=component, help="optional variant subdirectory")
        command.add_argument("--target", required=name == "pull")
        command.add_argument("--workspace", help="local workspace mapped by mctl")
    for name in ("copy", "mirror", "verify"):
        command = commands.add_parser(name)
        command.add_argument("directory", help="local image directory containing deployment.json")
        if name == "mirror":
            command.add_argument("--nfs-root", dest="destination", required=True,
                                 help="NFS root; project/deployment/variant appended")
        else:
            command.add_argument("--destination", help="exact destination artifact directory")
        command.add_argument("--target", help="mctl target; omit for a local filesystem path")
        command.add_argument("--workspace", help="local workspace mapped by mctl")
    command = commands.add_parser("list")
    command.add_argument("--project", type=component)
    command.add_argument("--json", action="store_true",
                         help="one JSON document; a bundle that fails verification is "
                              "listed with an error instead of aborting the listing")
    return result


def list_json(root):
    images = []
    for path in sorted(root.glob("**/deployment.json")):
        try:
            directory, data, _ = bundle(path.parent)
        except (DeployError, OSError, ValueError, KeyError, TypeError) as error:
            images.append({"directory": str(path.parent), "valid": False,
                           "error": str(error)})
            continue
        images.append({
            "directory": str(directory), "valid": True,
            "project": data["project"], "deployment": data["deployment"],
            "platform": data["platform"], "variant": data["variant"],
            "image": data["image"], "path": str(directory / data["image"]),
            "size": data["source"]["size"], "sha256": data["source"]["sha256"],
            "created_utc": data["created_utc"],
            "notes": str(directory / FILES[0]),
        })
    images.sort(key=lambda item: item.get("created_utc", ""), reverse=True)
    return {"root": str(root), "images": images}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in ("import", "pull"):
            import_image(args)
        elif args.command == "list":
            root = Path(args.root).expanduser()
            if args.project:
                root /= args.project
            if args.json:
                print(json.dumps(list_json(root), indent=2))
                return 0
            for path in sorted(root.glob("**/deployment.json")):
                directory, data, _ = bundle(path.parent)
                print(f"{data['project']}\t{data['source']['size']}\t{directory}")
        elif args.command == "verify" and not args.destination:
            if args.target:
                raise DeployError("--destination is required with --target")
            directory, _, _ = bundle(args.directory)
            print(f"verified {directory}")
        else:
            if not args.destination:
                raise DeployError("--destination is required")
            copy_image(args, mirror=args.command == "mirror",
                       verify=args.command == "verify")
    except (DeployError, OSError, ValueError, KeyError, TypeError,
            binascii.Error, argparse.ArgumentTypeError) as error:
        print(f"fw-deploy: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
