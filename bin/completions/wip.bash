#!/usr/bin/env bash
# Bash completion for the `wip` CLI.  Source from ~/.bashrc:
#   source ~/bin/completions/wip.bash

_wip_complete() {
	local cur prev words cword
	_init_completion 2>/dev/null || {
		cur="${COMP_WORDS[COMP_CWORD]}"
		prev="${COMP_WORDS[COMP_CWORD-1]}"
		cword=$COMP_CWORD
	}

	local commands="scan mode status info save load list forall help ws all"
	local actions="checkout push pull align set-upstream stash pop diff return hard-reset depends-on"
	local flags="--yes -y --force-mirror"
	local modes="author mirror default"

	# snapshot names for save/load/scan
	_wip_snapshots() {
		local d="$HOME/.wip/saved" f
		[[ -d "$d" ]] || return
		for f in "$d"/*.wip; do [[ -e "$f" ]] && basename "$f" .wip; done
	}

	if [[ $cword -eq 1 ]]; then
		COMPREPLY=($(compgen -W "$commands $actions" -- "$cur"))
		return
	fi

	# value completion after --mode
	if [[ "$prev" == "--mode" ]]; then
		COMPREPLY=($(compgen -W "author mirror" -- "$cur")); return
	fi

	case "${COMP_WORDS[1]}" in
		ws|all)   COMPREPLY=($(compgen -W "$actions $flags" -- "$cur")) ;;
		help)     COMPREPLY=($(compgen -W "$actions" -- "$cur")) ;;
		mode)     COMPREPLY=($(compgen -W "$modes" -- "$cur")) ;;
		load|save) COMPREPLY=($(compgen -W "$(_wip_snapshots)" -- "$cur")) ;;
		scan)     COMPREPLY=($(compgen -W "$(_wip_snapshots) --mode" -- "$cur")) ;;
		checkout|push|pull|align|set-upstream|stash|pop|diff|return|hard-reset|depends-on)
			COMPREPLY=($(compgen -W "$flags" -- "$cur")) ;;
		*)        COMPREPLY=($(compgen -W "$flags" -- "$cur")) ;;
	esac
}
complete -F _wip_complete wip
