#!/usr/bin/env bash
#
# setup_machine.sh <machine> [remote-user] [remote-home]
#
# Provision GH + SSH + AWS credentials onto a lab machine by copying them from
# THIS machine (your local WSL, which holds valid creds) into the target's NFS
# home. Fixes the classic errors from octl / gh / git on a freshly-used machine:
#   • "Could not authenticate using GH credentials … Run `gh auth login`"
#   • "SSH agent requested but SSH_AUTH_SOCK not-specified"
#
# Copies performed (local → <machine>:<remote-home>):
#   scp -r ~/.config/gh/*   → <remote-home>/.config/gh/
#   scp -r ~/.ssh/*         → <remote-home>/.ssh/
#   scp    ~/.aws/config    → <remote-home>/.aws/config
#
# Most lab machines share ONE NFS home, so provisioning any one of them
# populates the shared home for all of them.
#
# Usage:
#   setup_machine.sh <machine>                    # default remote user
#   setup_machine.sh <machine> <remote-user>      # explicit remote user
#
set -euo pipefail

source "$(dirname "$(readlink -f "$0")")/cli-local-config.sh"

MACHINE=${1:?"usage: setup_machine.sh <machine-name> [remote-user]"}
RUSER=${2:-${MCTL_DEFAULT_USER:-$(cli_local_default default-remote-user "$(id -un)")}}
RHOME=${3:-"/users/home/$RUSER"}   # lab NFS home; mctl provision passes the real one
DEST="$RUSER@$MACHINE"

SOPT="-o StrictHostKeyChecking=no -o ConnectTimeout=10"

echo "[setup_machine] provisioning '$MACHINE' (user=$RUSER, home=$RHOME) from local $HOME"

ssh $SOPT "$DEST" "mkdir -p '$RHOME/.config/gh' '$RHOME/.ssh' '$RHOME/.aws'"
[ -d "$HOME/.config/gh" ]  && scp $SOPT -r "$HOME/.config/gh/"* "$DEST:$RHOME/.config/gh/"
[ -f "$HOME/.aws/config" ] && scp $SOPT "$HOME/.aws/config"     "$DEST:$RHOME/.aws/config"

# SSH: copy only the OUTBOUND material (identity keys, config, known_hosts) —
# never scp the whole dir, which would clobber the host's authorized_keys.
for f in id_ed25519 id_ed25519.pub id_rsa id_rsa.pub config known_hosts; do
  [ -f "$HOME/.ssh/$f" ] && scp $SOPT "$HOME/.ssh/$f" "$DEST:$RHOME/.ssh/$f"
done

# INBOUND passwordless ssh: append this machine's public key to the host's
# authorized_keys (idempotent — never duplicates, never clobbers existing keys).
for pub in "$HOME/.ssh/id_ed25519.pub" "$HOME/.ssh/id_rsa.pub"; do
  [ -f "$pub" ] || continue
  PUB=$(cat "$pub")
  ssh $SOPT "$DEST" "touch '$RHOME/.ssh/authorized_keys'; grep -qxF \"$PUB\" '$RHOME/.ssh/authorized_keys' || printf '%s\n' \"$PUB\" >> '$RHOME/.ssh/authorized_keys'"
done

# Lock down the copied private keys + authorized_keys so ssh will accept them.
ssh $SOPT "$DEST" "chmod 700 '$RHOME/.ssh' 2>/dev/null || true; chmod 600 '$RHOME/.ssh/'* 2>/dev/null || true"

# octl/gh need an ssh-agent (SSH_AUTH_SOCK) in interactive login shells. The key
# is passphrase-protected, so use `keychain` (same as local WSL): it reuses or
# starts a per-host agent and prompts for the passphrase ONCE, then persists it
# across logins. Install an idempotent bootstrap into ~/.bashrc (replace any
# previous block so re-runs update it).
ssh $SOPT "$DEST" 'sed -i "/# >>> mctl ssh-agent bootstrap >>>/,/# <<< mctl ssh-agent bootstrap <<</d" ~/.bashrc 2>/dev/null; cat >> ~/.bashrc' <<'BOOTSTRAP'
# >>> mctl ssh-agent bootstrap >>>
if [ -n "${SSH_AUTH_SOCK:-}" ] && ssh-add -l >/dev/null 2>&1; then
  :  # a forwarded agent (ssh -A) already holds keys — keep using it
elif command -v keychain >/dev/null 2>&1; then
  eval "$(keychain --eval --quiet --agents ssh id_ed25519 2>/dev/null)"
elif [ -z "${SSH_AUTH_SOCK:-}" ]; then
  _agent_env="$HOME/.ssh/agent.$(hostname -s).env"
  [ -r "$_agent_env" ] && . "$_agent_env" >/dev/null 2>&1
  ssh-add -l >/dev/null 2>&1 || { (umask 077; ssh-agent -s > "$_agent_env" 2>/dev/null); . "$_agent_env" >/dev/null 2>&1; }
  unset _agent_env
fi
# <<< mctl ssh-agent bootstrap <<<
BOOTSTRAP

echo "[setup_machine] done: $MACHINE"
echo "[setup_machine] NOTE: open a fresh login on $MACHINE — keychain will ask"
echo "[setup_machine]       for your key passphrase ONCE, then octl/gh will work."
