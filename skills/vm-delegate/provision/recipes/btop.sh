# desc: btop resource monitor in ~/.local/bin (official static release; no sudo)
# where: remote
check() { [ -x ~/.local/bin/btop ] || command -v btop >/dev/null 2>&1; }
install() {
  case "$(uname -m)" in
    x86_64|amd64)  arch=x86_64 ;;
    aarch64|arm64) arch=aarch64 ;;
    *) echo "btop: unsupported architecture $(uname -m)" >&2; return 1 ;;
  esac
  tmp=$(mktemp -d) || return 1
  curl -fsSL "https://github.com/aristocratos/btop/releases/latest/download/btop-${arch}-unknown-linux-musl.tar.gz" \
    | tar xzf - -C "$tmp" || { rm -rf "$tmp"; return 1; }
  mkdir -p ~/.local/bin ~/.local/share/btop
  command install -m 755 "$tmp/btop/bin/btop" ~/.local/bin/btop
  rm -rf ~/.local/share/btop/themes && cp -r "$tmp/btop/themes" ~/.local/share/btop/
  rm -rf "$tmp"
  grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' ~/.bashrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
}
