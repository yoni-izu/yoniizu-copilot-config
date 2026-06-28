#!/bin/bash
# sync_all_workspaces.sh — daily "sync all workspaces" chore for sync-bot.
#
# For a project WIP root (~/projects/<project>/), for every workspace it:
#   1. scans + saves current state via wip-workspace (wip_funcs.sh)
#   2. stashes uncommitted changes in ALL repos (any branch)
#   3. returns every repo to its base/upstream branch
#   4. runs `elmcli ws sync` and `elmcli artifacts download` per workspace
#   5. restores the branches we had
#   6. pops the stashes we created
#
# Idempotent and safe: only pops stashes it created (matched by message),
# never force-pushes, never resets. Conflicts are reported, never forced.
#
# Usage:
#   sync_all_workspaces.sh [project] [--no-artifacts] [--dry-run]
#
#   project        WIP root under ~/projects (default: iridium)
#   --no-artifacts skip `elmcli artifacts download` (sync only)
#   --dry-run      show what would happen; touch nothing
#
# Exit code: 0 if all workspaces synced clean; 1 if any sync/artifact step or
# stash-pop hit a problem (details in the SUMMARY block).

set -o pipefail

PROJECT="iridium"
DO_ARTIFACTS=1
DRY_RUN=0

for arg in "$@"; do
	case "$arg" in
		--no-artifacts) DO_ARTIFACTS=0 ;;
		--dry-run)      DRY_RUN=1 ;;
		--*)            echo "Unknown flag: $arg" >&2; exit 2 ;;
		*)              PROJECT="$arg" ;;
	esac
done

WIP_ROOT="$HOME/projects/$PROJECT"
STASH_MSG="sync-all-auto-$(date +%Y%m%d-%H%M%S)"
SAVE_NAME="sync-all-${PROJECT}-$(date +%Y%m%d-%H%M%S)"

if [[ ! -d "$WIP_ROOT" ]]; then
	echo "ERROR: WIP root not found: $WIP_ROOT" >&2
	exit 2
fi
if [[ ! -f "$HOME/wip_funcs.sh" ]]; then
	echo "ERROR: ~/wip_funcs.sh not found (needed for wip-workspace ops)" >&2
	exit 2
fi

# shellcheck source=/dev/null
source "$HOME/wip_funcs.sh"

cd "$WIP_ROOT" || { echo "ERROR: cannot cd to $WIP_ROOT" >&2; exit 2; }

run() { # echo + execute, honoring --dry-run
	if [[ $DRY_RUN -eq 1 ]]; then echo "  [dry-run] $*"; return 0; fi
	"$@"
}

echo "============================================================"
echo "sync-all  project=$PROJECT  root=$WIP_ROOT"
echo "save=$SAVE_NAME  stash-tag=$STASH_MSG  artifacts=$DO_ARTIFACTS  dry-run=$DRY_RUN"
echo "============================================================"

# --- 1. Scan + save current state -------------------------------------------
echo "[1/6] Scanning + saving current state via wip-workspace..."
wip_scan >/dev/null
if [[ ${#wip_info[@]} -eq 0 ]]; then
	echo "ERROR: no repos found after scan — is $WIP_ROOT a valid WIP root?" >&2
	exit 2
fi
run wip_save "$SAVE_NAME"

# Collect the list of workspaces (subdirs with a .repo manifest)
WORKSPACES=()
for ws_dir in "$WIP_ROOT"/*/; do
	[[ -f "$ws_dir/.repo/metadata.json" ]] && WORKSPACES+=("$(basename "$ws_dir")")
done
echo "  workspaces: ${WORKSPACES[*]:-<none>}  (${#wip_info[@]} repos)"

# --- 2. Stash uncommitted changes in ALL repos (any branch) -----------------
echo "[2/6] Stashing uncommitted changes (all repos)..."
STASHED=()
for key in "${!wip_info[@]}"; do
	ws_name=${key%%:*}
	rel_path=${wip_info[$key]%%:*}
	repo_path="$WIP_ROOT/$ws_name/$rel_path"
	[[ -d "$repo_path/.git" ]] || continue
	if [[ -n $(git -C "$repo_path" status --porcelain --untracked-files=no 2>/dev/null) ]]; then
		if [[ $DRY_RUN -eq 1 ]]; then
			echo "  [dry-run] would stash: $ws_name/$rel_path"
			continue
		fi
		if git -C "$repo_path" stash push -m "$STASH_MSG" >/dev/null 2>&1; then
			STASHED+=("$repo_path")
			echo "  stashed: $ws_name/$rel_path"
		else
			echo "  WARN: stash failed: $ws_name/$rel_path"
		fi
	fi
done
[[ ${#STASHED[@]} -eq 0 ]] && echo "  (nothing to stash)"

# --- 3. Return every repo to its base/upstream branch -----------------------
echo "[3/6] Checking out base/upstream branches..."
if [[ $DRY_RUN -eq 1 ]]; then echo "  [dry-run] wip_return (all repos -> upstream)"; else _wip_action return; fi

# --- 4. Sync + download artifacts per workspace -----------------------------
echo "[4/6] Syncing + downloading artifacts per workspace..."
FAILED_WS=()
for ws in "${WORKSPACES[@]}"; do
	ws_path="$WIP_ROOT/$ws"
	echo "  --- $ws ---"
	ws_ok=1
	if ! ( cd "$ws_path" && run elmcli --plain ws sync ); then
		echo "    SYNC FAILED: $ws"
		ws_ok=0
	fi
	if [[ $DO_ARTIFACTS -eq 1 ]]; then
		if ! ( cd "$ws_path" && run elmcli --plain artifacts download ); then
			echo "    ARTIFACTS FAILED: $ws"
			ws_ok=0
		fi
	fi
	[[ $ws_ok -eq 0 ]] && FAILED_WS+=("$ws")
done

# --- 5. Restore the branches we had -----------------------------------------
echo "[5/6] Restoring original branches..."
if [[ $DRY_RUN -eq 1 ]]; then echo "  [dry-run] wip_checkout (restore recorded branches)"; else _wip_action checkout; fi

# --- 6. Pop the stashes we created ------------------------------------------
echo "[6/6] Popping stashes..."
POP_CONFLICTS=()
for repo_path in "${STASHED[@]}"; do
	ref=$(git -C "$repo_path" stash list 2>/dev/null | grep -F "$STASH_MSG" | head -1 | sed 's/:.*//')
	[[ -z "$ref" ]] && { echo "  WARN: stash vanished: $repo_path"; continue; }
	if git -C "$repo_path" stash pop "$ref" >/dev/null 2>&1; then
		echo "  popped: ${repo_path#"$WIP_ROOT"/}"
	else
		echo "  POP CONFLICT (stash kept): ${repo_path#"$WIP_ROOT"/}"
		POP_CONFLICTS+=("$repo_path")
	fi
done
[[ ${#STASHED[@]} -eq 0 ]] && echo "  (no stashes to pop)"

# --- Summary ----------------------------------------------------------------
echo "============================================================"
echo "SUMMARY  project=$PROJECT"
echo "  workspaces synced : $(( ${#WORKSPACES[@]} - ${#FAILED_WS[@]} ))/${#WORKSPACES[@]}"
echo "  repos stashed     : ${#STASHED[@]}"
echo "  saved state       : $SAVE_NAME  (~/wip/$SAVE_NAME.wip)"
[[ ${#FAILED_WS[@]}    -gt 0 ]] && echo "  FAILED workspaces : ${FAILED_WS[*]}"
[[ ${#POP_CONFLICTS[@]} -gt 0 ]] && echo "  POP CONFLICTS     : ${POP_CONFLICTS[*]#"$WIP_ROOT"/}"
if [[ ${#FAILED_WS[@]} -eq 0 && ${#POP_CONFLICTS[@]} -eq 0 ]]; then
	echo "  status            : OK"
	echo "============================================================"
	exit 0
else
	echo "  status            : ATTENTION NEEDED"
	echo "  recover branches  : cd $WIP_ROOT && wip_load $SAVE_NAME && wip_checkout"
	echo "============================================================"
	exit 1
fi
