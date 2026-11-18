# desc: GitHub Copilot CLI in ~/.local/bin (official installer; no node needed)
# where: remote
check() { [ -x ~/.local/bin/copilot ] || command -v copilot >/dev/null 2>&1; }
install() {
  curl -fsSL https://gh.io/copilot-install | PREFIX="$HOME/.local" bash
  grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
}
todo() {
  grep -q '"logged_in_users"' ~/.copilot/config.json 2>/dev/null ||
    echo "Copilot CLI is not logged in: ssh -t $MCTL_DEST '~/.local/bin/copilot login'"
}
