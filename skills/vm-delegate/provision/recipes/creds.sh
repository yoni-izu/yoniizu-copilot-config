# desc: gh / ssh / aws credentials copied from this machine (setup_machine.sh)
# where: local
# Local recipe: runs on WSL with MCTL_DEST ([user@]host), MCTL_HOST,
# MCTL_LOGIN, MCTL_REMOTE_HOME and `r` (ssh to the target) in scope.
check() {
  r 'gh auth status >/dev/null 2>&1 && [ -f ~/.ssh/id_ed25519 ] && [ -f ~/.aws/config ]'
}
install() {
  "$HOME/bin/setup_machine.sh" "$MCTL_HOST" "$MCTL_LOGIN" "$MCTL_REMOTE_HOME"
}
todo() {
  r 'grep -q "mctl ssh-agent bootstrap" ~/.bashrc' >/dev/null 2>&1 &&
    echo "mctl uses agent forwarding; only a login WITHOUT ssh -A asks (once) for the key passphrase via keychain"
}
