#!/usr/bin/env python3
"""Local mirror + search over the Platform Engineering docs (ip.infra.devops.apps/docs).

The Platform team keeps its maintained documentation in
Elmnt-Internal/ip.infra.devops.apps under docs/ (published at
https://platform-engineering-docs.elmnt.com/). Confluence space "platform" holds
older/archived pages. This tool keeps a sparse, read-only clone and lets agents
search it without re-fetching from GitHub.

Usage:
  devops_docs.py sync                      # clone or fast-forward the sparse mirror
  devops_docs.py status                    # mirror path, commit, age
  devops_docs.py search <regex> [--area A] [--type T] [-n N] [--titles]
  devops_docs.py show <path|slug> [--section HEADING] [--max-lines N]
  devops_docs.py list [--area A] [--type T]
  devops_docs.py areas
  devops_docs.py index [--out FILE]        # regenerate references/docs-index.md
  devops_docs.py skills                    # list the repo's own agent skills (.github/skills)
"""
import argparse
import os
import re
import subprocess
import sys
import time

REPO = "git@github.com:Elmnt-Internal/ip.infra.devops.apps.git"
MIRROR = os.path.expanduser(os.environ.get("DEVOPS_DOCS_MIRROR", "~/.cache/devops-expert/apps"))
DOCS = os.path.join(MIRROR, "docs")
SITE = "https://platform-engineering-docs.elmnt.com/"
GH_BLOB = "https://github.com/Elmnt-Internal/ip.infra.devops.apps/blob/main/"
SPARSE = ["/docs/", "/.github/skills/", "/.github/copilot-instructions.md", "/AGENTS.md", "/README.md"]
STALE_DAYS = 7
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, check=check, text=True, capture_output=True)


def sync():
    if not os.path.isdir(os.path.join(MIRROR, ".git")):
        os.makedirs(os.path.dirname(MIRROR), exist_ok=True)
        run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--sparse", REPO, MIRROR])
    run(["git", "sparse-checkout", "set", "--no-cone", *SPARSE], cwd=MIRROR)
    run(["git", "fetch", "-q", "--depth", "1", "origin", "main"], cwd=MIRROR)
    run(["git", "reset", "-q", "--hard", "origin/main"], cwd=MIRROR)
    status()


def mirror_age_days():
    head = os.path.join(MIRROR, ".git", "FETCH_HEAD")
    if not os.path.exists(head):
        head = os.path.join(MIRROR, ".git", "HEAD")
    return (time.time() - os.path.getmtime(head)) / 86400


def ensure_mirror():
    if not os.path.isdir(DOCS):
        print(f"[devops-docs] no mirror at {MIRROR}; syncing...", file=sys.stderr)
        sync()
    elif mirror_age_days() > STALE_DAYS:
        print(f"[devops-docs] mirror is {mirror_age_days():.0f} days old; run `devops_docs.py sync`",
              file=sys.stderr)


def status():
    if not os.path.isdir(os.path.join(MIRROR, ".git")):
        print(f"mirror: missing ({MIRROR}) - run sync")
        return
    rev = run(["git", "log", "-1", "--format=%h %cI %s"], cwd=MIRROR).stdout.strip()
    print(f"mirror: {MIRROR}\ncommit: {rev}\nfetched: {mirror_age_days():.1f} days ago")


def front_matter(text):
    m = re.match(r"---\n(.*?)\n---\n?", text, re.S)
    fm = {}
    if not m:
        return fm, text
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"').strip("'")
    return fm, text[m.end():]


def site_url(rel, fm):
    if fm.get("permalink"):
        return SITE.rstrip("/") + fm["permalink"]
    if rel.endswith("README.md") or rel.endswith("index.md"):
        return SITE + rel.rsplit("/", 1)[0] + "/" if "/" in rel else SITE
    return SITE + rel[:-3] + ".html"


def iter_docs():
    for root, dirs, files in os.walk(DOCS):
        dirs[:] = [d for d in dirs if not d.startswith(("_", "."))]
        for f in sorted(files):
            if not f.endswith(".md") or f.startswith("_"):
                continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, DOCS)
            with open(path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
            fm, body = front_matter(text)
            title = fm.get("title")
            if not title:
                h = re.search(r"^#\s+(.+)$", body, re.M)
                title = h.group(1).strip() if h else rel
            yield {"rel": rel, "path": path, "fm": fm, "body": body, "title": title}


def match_filters(d, area, typ):
    return (not area or d["fm"].get("area") == area) and (not typ or d["fm"].get("type") == typ)


def cmd_search(a):
    ensure_mirror()
    rx = re.compile(a.pattern, re.I)
    hits = []
    for d in iter_docs():
        if not match_filters(d, a.area, a.type):
            continue
        meta = " ".join([d["title"], d["fm"].get("summary", ""), d["rel"]])
        lines = [] if a.titles else [(i + 1, l) for i, l in enumerate(d["body"].splitlines()) if rx.search(l)]
        meta_hit = bool(rx.search(meta))
        if not lines and not meta_hit:
            continue
        hits.append((meta_hit * 100 + len(lines), d, lines))
    hits.sort(key=lambda h: -h[0])
    for _, d, lines in hits[: a.n]:
        fm = d["fm"]
        print(f"## {d['title']}  [{fm.get('area', '-')}/{fm.get('type', '-')}]")
        print(f"   docs/{d['rel']}  ->  {site_url(d['rel'], fm)}")
        if fm.get("summary"):
            print(f"   {fm['summary']}")
        for ln, l in lines[:5]:
            print(f"   L{ln}: {l.strip()[:200]}")
        if len(lines) > 5:
            print(f"   ... {len(lines) - 5} more matching lines")
    if not hits:
        print("no matches")


def resolve(target):
    cand = [target, target + ".md", os.path.join("how-to", target), os.path.join("how-to", target + ".md")]
    for c in cand:
        p = os.path.join(DOCS, c)
        if os.path.isfile(p):
            return p
    slug = os.path.basename(target).removesuffix(".md")
    found = [d["path"] for d in iter_docs() if os.path.basename(d["rel"]).removesuffix(".md") == slug]
    if len(found) == 1:
        return found[0]
    if found:
        sys.exit("ambiguous: " + ", ".join(os.path.relpath(f, DOCS) for f in found))
    sys.exit(f"not found: {target} (try `search`)")


def cmd_show(a):
    ensure_mirror()
    path = resolve(a.target)
    rel = os.path.relpath(path, DOCS)
    with open(path, encoding="utf-8", errors="replace") as fh:
        fm, body = front_matter(fh.read())
    print(f"# docs/{rel}\n# {site_url(rel, fm)}\n# {GH_BLOB}docs/{rel}")
    for k in ("owner", "status", "last_reviewed", "summary"):
        if fm.get(k):
            print(f"# {k}: {fm[k]}")
    lines = body.splitlines()
    if a.section:
        rx = re.compile(r"^(#+)\s+.*" + re.escape(a.section), re.I)
        start = next((i for i, l in enumerate(lines) if rx.match(l)), None)
        if start is None:
            sys.exit(f"section not found: {a.section}")
        level = len(rx.match(lines[start]).group(1))
        end = next((i for i in range(start + 1, len(lines))
                    if re.match(r"^#{1,%d}\s" % level, lines[i])), len(lines))
        lines = lines[start:end]
    print("\n".join(lines[: a.max_lines]))
    if len(lines) > a.max_lines:
        print(f"\n... truncated ({len(lines)} lines); use --max-lines or --section")


def cmd_list(a):
    ensure_mirror()
    for d in iter_docs():
        if match_filters(d, a.area, a.type):
            print(f"docs/{d['rel']}\t{d['fm'].get('area', '-')}\t{d['title']}")


def cmd_areas(_):
    ensure_mirror()
    counts = {}
    for d in iter_docs():
        k = (d["fm"].get("area", "-"), d["fm"].get("type", "-"))
        counts[k] = counts.get(k, 0) + 1
    for (area, typ), n in sorted(counts.items()):
        print(f"{area:20} {typ:12} {n}")


def cmd_index(a):
    ensure_mirror()
    out = a.out or os.path.join(SKILL_DIR, "references", "docs-index.md")
    groups = {}
    for d in iter_docs():
        groups.setdefault(d["rel"].split("/")[0] if "/" in d["rel"] else "(root)", []).append(d)
    rev = run(["git", "log", "-1", "--format=%h %cs"], cwd=MIRROR).stdout.strip()
    lines = [
        "# Platform docs index (generated)",
        "",
        f"Generated by `scripts/devops_docs.py index` from ip.infra.devops.apps@{rev}. "
        "Do not hand-edit; re-run after `sync`.",
        f"Site: {SITE} (GitHub-SSO gated). Local mirror: `{MIRROR.replace(os.path.expanduser('~'), '~')}/docs/`.",
        "Read any page with `devops_docs.py show <path>`; skip `docs/octl/*` (use `octl <cmd> --help`).",
        "",
    ]
    for g in sorted(groups):
        if g == "octl":
            lines += [f"## octl/ ({len(groups[g])} auto-generated CLI pages — see references/octl.md)", ""]
            continue
        lines += [f"## {g}/", "", "| Path | Area | Title — summary |", "|---|---|---|"]
        for d in sorted(groups[g], key=lambda x: x["rel"]):
            s = d["fm"].get("summary", "").replace("|", "\\|")
            t = d["title"].replace("|", "\\|")
            lines.append(f"| `{d['rel']}` | {d['fm'].get('area', '-')} | {t}{' — ' + s if s else ''} |")
        lines.append("")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"wrote {out} ({len(lines)} lines)")


def cmd_skills(_):
    ensure_mirror()
    base = os.path.join(MIRROR, ".github", "skills")
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name, "SKILL.md")
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8", errors="replace") as fh:
            fm, _ = front_matter(fh.read())
        print(f"{name:28} {fm.get('description', '')[:160]}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync").set_defaults(fn=lambda a: sync())
    sub.add_parser("status").set_defaults(fn=lambda a: status())
    s = sub.add_parser("search")
    s.add_argument("pattern")
    s.add_argument("--area")
    s.add_argument("--type")
    s.add_argument("-n", type=int, default=10)
    s.add_argument("--titles", action="store_true", help="match title/summary/path only")
    s.set_defaults(fn=cmd_search)
    s = sub.add_parser("show")
    s.add_argument("target")
    s.add_argument("--section")
    s.add_argument("--max-lines", type=int, default=250)
    s.set_defaults(fn=cmd_show)
    s = sub.add_parser("list")
    s.add_argument("--area")
    s.add_argument("--type")
    s.set_defaults(fn=cmd_list)
    sub.add_parser("areas").set_defaults(fn=cmd_areas)
    s = sub.add_parser("index")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_index)
    sub.add_parser("skills").set_defaults(fn=cmd_skills)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
