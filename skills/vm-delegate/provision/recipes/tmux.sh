# desc: tmux persistent terminal multiplexer (system package)
# where: remote
check() { command -v tmux >/dev/null 2>&1 && tmux -V >/dev/null 2>&1; }
install() {
  local -a cmd
  local uid
  if command -v apt-get >/dev/null 2>&1; then
    cmd=(env DEBIAN_FRONTEND=noninteractive apt-get install -y tmux)
  elif command -v dnf >/dev/null 2>&1; then
    cmd=(dnf install -y tmux)
  elif command -v yum >/dev/null 2>&1; then
    cmd=(yum install -y tmux)
  else
    echo "tmux: no supported system package manager (apt-get, dnf or yum)" >&2
    return 1
  fi
  uid=$(id -u) || { echo "tmux: cannot determine effective user" >&2; return 1; }
  case "$uid" in
    ""|*[!0-9]*) echo "tmux: invalid effective user id" >&2; return 1 ;;
  esac
  if [ "$uid" = 0 ]; then
    "${cmd[@]}" && return 0
  elif command -v sudo >/dev/null 2>&1; then
    sudo -A "${cmd[@]}" && return 0
  else
    echo "tmux: installation requires root or non-interactive sudo -A" >&2
    return 1
  fi
  echo "tmux: system package installation failed; check privileges and provisioning log" >&2
  return 1
}
