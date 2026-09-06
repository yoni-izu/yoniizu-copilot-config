# Local CLIs (version controlled)

Home for the standalone command-line tools that back several skills. They live
here so they are version controlled, even though `~/.copilot/bin` is not their
natural home. They are exposed on `PATH` from `~/bin` (or `$PREFIX`) via
`install.sh`.

| Tool    | Purpose                                                                 |
|---------|-------------------------------------------------------------------------|
| `wip`   | Multi-repo / workspace git operations (see the `wip-workspace` skill).  |
| `vmctl` | Delegate FW workspace work to the EC2 VM mirror (see `vm-delegate`).     |

## Install

```bash
~/.copilot/bin/install.sh          # symlink wip + vmctl into ~/bin (default)
PREFIX=~/.local/bin ~/.copilot/bin/install.sh
~/.copilot/bin/install.sh --copy   # copy instead of symlink (e.g. a remote VM)
```

Symlink is the default: after `git pull` the installed commands update with no
re-install. Use `--copy` on machines where the repo is not checked out (the VM).
