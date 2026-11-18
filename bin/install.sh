#!/usr/bin/env bash
# Install the repository's local CLIs into $PREFIX (default: ~/bin).
# Usage: ./install.sh [--link|--copy]
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX="${PREFIX:-$HOME/bin}"
MODE=link
FILES=(
	"bin/wip:wip"
	"bin/mctl:mctl"
	"bin/vmctl:vmctl"
	"bin/wip-deploy:wip-deploy"
	"bin/setup_machine.sh:setup_machine.sh"
	"bin/lsctl:lsctl"
	"bin/bmcjob:bmcjob"
	"bin/fw-deploy:fw-deploy"
	"bin/cli-local-config.sh:cli-local-config.sh"
	"bin/completions/wip.bash:completions/wip.bash"
)

for arg in "$@"; do
	case "$arg" in
		--copy) MODE=copy ;;
		--link) MODE=link ;;
		-h|--help) sed -n '2,3p' "${BASH_SOURCE[0]}"; exit 0 ;;
		*) echo "install.sh: unknown option '$arg'" >&2; exit 2 ;;
	esac
done

for entry in "${FILES[@]}"; do
	src="$REPO_DIR/${entry%%:*}"
	dst="$PREFIX/${entry#*:}"
	[[ -f "$src" ]] || { echo "install.sh: missing source $src" >&2; exit 1; }
	if [[ -e "$dst" || -L "$dst" ]]; then
		if [[ "$MODE" == link && -L "$dst" && "$(readlink -f "$dst")" == "$src" ]]; then
			continue
		fi
		if [[ "$MODE" == copy && ! -L "$dst" && -f "$dst" ]] && cmp -s "$src" "$dst"; then
			continue
		fi
		echo "install.sh: refusing to overwrite $dst" >&2
		exit 1
	fi
done

for entry in "${FILES[@]}"; do
	src="$REPO_DIR/${entry%%:*}"
	dst="$PREFIX/${entry#*:}"
	if [[ -e "$dst" || -L "$dst" ]]; then
		echo "unchanged $dst"
		continue
	fi
	mkdir -p "$(dirname "$dst")"
	if [[ "$MODE" == copy ]]; then
		cp -p "$src" "$dst"
		echo "copied $dst"
	else
		ln -s "$src" "$dst"
		echo "linked $dst"
	fi
done

case ":$PATH:" in
	*":$PREFIX:"*) ;;
	*) echo "note: $PREFIX is not on your PATH" >&2 ;;
esac
