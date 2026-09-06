#!/usr/bin/env bash
# Bootstrap a Palladium station / EDK host on first connection.
# Copies SSH keypair and shell helpers, wires up ~/.bashrc, and drops a marker file.
#
# Usage:
#   ./bootstrap_station.sh <host> [--force]
#   ./bootstrap_station.sh pldm-edk0-z2a
#
# <host> is anything ssh can reach (hostname or user@host). On first run it copies:
#   ~/.ssh/id_ed25519, ~/.ssh/id_ed25519.pub, ~/bin/wip (+ completion),
#   ~/wip_funcs.sh, ~/.bash_aliases, ~/my_funcs.sh
# Only files that exist locally are copied. Re-run with --force to refresh.

set -euo pipefail

MARKER='~/.palladium_bootstrapped'
FORCE=0
HOST=""

for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "unknown option: $arg" >&2; exit 2 ;;
    *) HOST="$arg" ;;
  esac
done

if [[ -z "$HOST" ]]; then
  echo "usage: $0 <host> [--force]" >&2
  exit 2
fi

# --- detect prior bootstrap -------------------------------------------------
if [[ "$FORCE" -eq 0 ]] && ssh "$HOST" "test -f $MARKER" 2>/dev/null; then
  echo "[$HOST] already bootstrapped (use --force to refresh). Nothing to do."
  exit 0
fi

echo "[$HOST] bootstrapping..."

# --- ensure remote ~/.ssh and ~/bin exist with correct perms ---------------
ssh "$HOST" 'mkdir -p ~/.ssh ~/bin/completions && chmod 700 ~/.ssh'

# --- copy files that exist locally -----------------------------------------
copy_if_present() {
  local src="$1" dest="$2" mode="${3:-}"
  if [[ -f "$src" ]]; then
    echo "  -> $src"
    scp -q "$src" "$HOST:$dest"
    [[ -n "$mode" ]] && ssh "$HOST" "chmod $mode $dest"
  else
    echo "  (skip, missing locally: $src)"
  fi
}

copy_if_present "$HOME/.ssh/id_ed25519"      '~/.ssh/id_ed25519'     600
copy_if_present "$HOME/.ssh/id_ed25519.pub"  '~/.ssh/id_ed25519.pub' 644
copy_if_present "$HOME/wip_funcs.sh"         '~/wip_funcs.sh'
copy_if_present "$HOME/.bash_aliases"        '~/.bash_aliases'
copy_if_present "$HOME/my_funcs.sh"          '~/my_funcs.sh'
copy_if_present "$HOME/bin/wip"              '~/bin/wip'                   755
copy_if_present "$HOME/bin/completions/wip.bash" '~/bin/completions/wip.bash'

# --- make ~/.bashrc source the helpers (idempotent) ------------------------
ssh "$HOST" 'bash -s' <<'REMOTE'
touch ~/.bashrc
for f in wip_funcs.sh my_funcs.sh; do
  line="[ -f ~/$f ] && source ~/$f"
  grep -qxF "$line" ~/.bashrc || echo "$line" >> ~/.bashrc
done
# ensure ~/bin (holds the `wip` CLI) is on PATH + load its completion
grep -qxF 'export PATH="$HOME/bin:$PATH"' ~/.bashrc || echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
wipcomp='[ -f ~/bin/completions/wip.bash ] && source ~/bin/completions/wip.bash'
grep -qxF "$wipcomp" ~/.bashrc || echo "$wipcomp" >> ~/.bashrc
# .bash_aliases is sourced by default ~/.bashrc on most distros; ensure it is.
line='[ -f ~/.bash_aliases ] && source ~/.bash_aliases'
grep -qF '.bash_aliases' ~/.bashrc || echo "$line" >> ~/.bashrc
REMOTE

# --- drop marker ------------------------------------------------------------
ssh "$HOST" "date -u +'%Y-%m-%dT%H:%M:%SZ' > $MARKER"

echo "[$HOST] bootstrap complete."
