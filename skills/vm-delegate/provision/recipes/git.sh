# desc: git identity (from local git config) + github.com in known_hosts
# where: remote
# env: GIT_NAME GIT_EMAIL are exported from the local git config.
check() {
  [ -n "$(git config --global user.email)" ] && ssh-keygen -F github.com >/dev/null 2>&1
}
install() {
  [ -n "$(git config --global user.name)" ]  || git config --global user.name  "$GIT_NAME"
  [ -n "$(git config --global user.email)" ] || git config --global user.email "$GIT_EMAIL"
  mkdir -p ~/.ssh && chmod 700 ~/.ssh
  ssh-keygen -F github.com >/dev/null 2>&1 || ssh-keyscan -t ed25519,rsa github.com >> ~/.ssh/known_hosts 2>/dev/null
}
