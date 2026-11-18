# desc: Neo (neo-brain) for neo-ask: ~/neo + ~/ai-toolkit marketplaces, plugins, ~/.neo/config
# where: remote
# needs: git creds copilot
NEO_REPO=git@github.com:Elmnt-Internal/ip.infra.vlsi.arch.neo.git
TOOLKIT_REPO=git@github.com:Elmnt-Internal/module.infra.ai_group.toolkit.git
PLUGINS="neo-brain@neo neo-poc@neo neo-ingest@neo hwarch-specs@ai-toolkit"
MIRRORS=$HOME/projects/iridium/rtl/mirrors
CP=$HOME/.local/bin/copilot
check() {
  [ -d ~/neo/.git ] && [ -d ~/ai-toolkit/.git ] && grep -q '^NEO_MIRRORS=' ~/.neo/config 2>/dev/null || return 1
  local have; have=$("$CP" plugin list 2>/dev/null) || return 1
  for p in $PLUGINS; do grep -q "${p%@*}" <<<"$have" || return 1; done
}
_clone() { if [ -d "$2/.git" ]; then git -C "$2" pull --ff-only -q; else git clone -q "$1" "$2"; fi; }
install() {
  export GIT_TERMINAL_PROMPT=0
  _clone "$NEO_REPO" ~/neo
  _clone "$TOOLKIT_REPO" ~/ai-toolkit
  "$CP" plugin marketplace list 2>/dev/null | grep -qw neo || "$CP" plugin marketplace add ~/neo
  "$CP" plugin marketplace list 2>/dev/null | grep -q 'ai-toolkit' || "$CP" plugin marketplace add ~/ai-toolkit
  for p in $PLUGINS; do "$CP" plugin install "$p"; done
  mkdir -p ~/.neo
  for kv in "NEO_MIRRORS=$MIRRORS/a1" "NEO_A2_MIRRORS=$MIRRORS/a2"; do
    sed -i "/^${kv%%=*}=/d" ~/.neo/config 2>/dev/null || true; echo "$kv" >> ~/.neo/config
    grep -qxF "export $kv" ~/.bashrc || echo "export $kv" >> ~/.bashrc
  done
}
