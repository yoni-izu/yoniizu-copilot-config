#!/usr/bin/env bash

cli_local_default() {
	local path="$HOME/.copilot-private/cli/$1" value=""

	if [[ -e "$path" ]]; then
		[[ -f "$path" && -r "$path" ]] || {
			echo "cannot read CLI setting: $path" >&2
			return 1
		}
		IFS= read -r value < "$path" || [[ -n "$value" ]] || {
			echo "empty CLI setting: $path" >&2
			return 1
		}
		[[ -n "$value" ]] || {
			echo "empty CLI setting: $path" >&2
			return 1
		}
		printf '%s\n' "$value"
	else
		printf '%s\n' "$2"
	fi
}
