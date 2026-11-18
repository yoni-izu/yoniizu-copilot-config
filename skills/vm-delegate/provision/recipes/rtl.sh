# desc: Neo's bare RTL mirrors (A1 + A2) cloned ON THE VM via mirror_repos.sh — large, slow
# where: remote
# needs: neo
# timeout: 10800
# RTL policy: RTL is cloned and read only on the VM; nothing is copied back.
MIRRORS=$HOME/projects/iridium/rtl/mirrors
SCRIPT=$HOME/neo/plugins/neo-brain/scripts/mirror_repos.sh
check() {
  for c in a1 a2; do ls -d "$MIRRORS/$c"/*.git >/dev/null 2>&1 || return 1; done
}
install() {
  export GIT_TERMINAL_PROMPT=0
  [ -f "$SCRIPT" ] || { echo "neo is not installed (run the neo recipe)" >&2; return 1; }
  mkdir -p "$MIRRORS/a1" "$MIRRORS/a2"
  bash "$SCRIPT" --chip a1 --mirror-dir "$MIRRORS/a1" -j 8
  bash "$SCRIPT" --chip a2 --mirror-dir "$MIRRORS/a2" -j 8
}
