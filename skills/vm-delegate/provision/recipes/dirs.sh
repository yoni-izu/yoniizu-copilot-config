# desc: mctl/wip state directories (~/.mctl/jobs, ~/.wip/saved, ~/bin)
# where: remote
check()   { [ -d ~/.mctl/jobs ] && [ -d ~/.wip/saved ] && [ -d ~/bin ]; }
install() { mkdir -p ~/.mctl/jobs ~/.wip/saved ~/bin; }
