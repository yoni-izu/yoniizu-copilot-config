# Local CLIs

Custom CLI sources belong in this repository. `bin/` contains the standalone
commands; domain-owned implementations can also live with their skills.
Personal records, credentials, machine-specific defaults, and runtime state
must remain outside the repository. `neo-ask` is excluded while its ownership
is unclear.

| Tool | Purpose |
|------|---------|
| `wip`, `wip-deploy` | Multi-repo workspace operations and remote installation |
| `mctl`, `vmctl`, `setup_machine.sh` | Machine delegation and compatibility helpers |
| `lsctl`, `bmcjob` | Local lab console and BMC job clients |
| `fw-deploy` | Launcher for `skills/fw-deploy/scripts/fw_deploy.py` |

The local `bmcjob` client is different from the BMC-side script in
`skills/machine-pilot/scripts/bmcjob`; both are retained.

## Install

```bash
~/.copilot/bin/install.sh
PREFIX=~/.local/bin ~/.copilot/bin/install.sh
~/.copilot/bin/install.sh --copy
```

The installer links commands and the `wip` completion by default. It checks all
destinations first and refuses to overwrite different files or links; move or
back up existing installations explicitly before running it. `--copy` copies
the files instead. Commands that use skills or other repository files still
require those dependencies on the target machine.

Machine-specific defaults, when needed, are single-line files under
`~/.copilot-private/cli/`:

| File | Used by | Fallback when absent |
|------|---------|----------------------|
| `default-remote-user` | `mctl`, `setup_machine.sh` | Local login name |
| `lab-bmc-host` | `lsctl`, local `bmcjob` | No host; set an explicit override |
| `lsctl-password-file` | `lsctl` | `~/.pilot/bmc.pw` |

Existing command-line and environment overrides take precedence. These files
contain configuration only, not passwords; credentials stay in their existing
private stores.
