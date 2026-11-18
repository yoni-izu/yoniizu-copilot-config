# desc: the wip multi-repo CLI (wip-deploy; redeployed when the local copy changes)
# where: local
check() {
  local l rm; l=$(sha256sum "$HOME/bin/wip" | cut -d' ' -f1)
  rm=$(r 'sha256sum ~/bin/wip 2>/dev/null | cut -d" " -f1')
  [ -n "$rm" ] && [ "$rm" = "$l" ]
}
install() { "$HOME/bin/wip-deploy" "$MCTL_DEST" --force; }
