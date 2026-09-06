#!/usr/bin/env bash
# Install the version-controlled CLIs (wip, vmctl) into a local bin directory.
#
# These scripts live in ~/.copilot/bin (version controlled here even though it is
# not their natural home) and are exposed on PATH by symlinking — so a `git pull`
# of this repo instantly updates the installed commands with no re-install.
#
# Usage:
#   ./install.sh                 # symlink into ~/bin (default)
#   PREFIX=~/.local/bin ./install.sh
#   ./install.sh --copy          # copy instead of symlink (e.g. for a remote VM)
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/bin}"
MODE=symlink
TOOLS=(wip vmctl)

for a in "$@"; do
  case "$a" in
    --copy)   MODE=copy ;;
    --link)   MODE=symlink ;;
    -h|--help)
      sed -n '2,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "install.sh: unknown option '$a'" >&2; exit 2 ;;
  esac
done

mkdir -p "$PREFIX"

for t in "${TOOLS[@]}"; do
  src="$SRC_DIR/$t"
  dst="$PREFIX/$t"
  [[ -f "$src" ]] || { echo "install.sh: missing source $src" >&2; exit 1; }
  chmod +x "$src"
  if [[ "$MODE" == copy ]]; then
    install -m 0755 "$src" "$dst"
    echo "copied   $t -> $dst"
  else
    ln -sfn "$src" "$dst"
    echo "linked   $t -> $dst"
  fi
done

case ":$PATH:" in
  *":$PREFIX:"*) ;;
  *) echo "note: $PREFIX is not on your PATH — add it to your shell rc." >&2 ;;
esac
