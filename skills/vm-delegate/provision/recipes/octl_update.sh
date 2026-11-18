# desc: update octl to the latest release (at most once a day)
# where: remote
STAMP=~/.mctl/provisioned/octl_update
check() { [ -n "$(find "$STAMP" -mmin -1440 2>/dev/null)" ]; }
install() { octl update && mkdir -p "${STAMP%/*}" && touch "$STAMP"; }
