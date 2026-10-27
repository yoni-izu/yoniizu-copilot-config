#!/usr/bin/env bash
# oam_exec.sh — run shell commands on the OAM consoles of an ELS tray and
# capture each reply to its own file.
#
# Runs from the WORKSTATION. This is the only console input path proven to work
# on ELS100: `ssh -tt -p 220<n> root@<bmc>`. Driving the consoles from the BMC
# itself does NOT work — see the private machine-pilot pitfalls reference.
#
#   oam_exec.sh [-b BMC] [-o OUTDIR] [-d LIST] [-w SECS] [-t SECS] 'cmd' ['cmd'...]
#
#   -b BMC     BMC host/IP           (default from private station config or $BMC)
#   -o OUTDIR  capture directory     (default ~/.pilot/evidence/oam-exec/<UTC>)
#   -d LIST    OAMs, e.g. "0 3 5"    (default 0..7)
#   -w SECS    settle after each cmd (default 5)
#   -t SECS    ssh timeout per OAM   (default 40; raise for slow dumps)
#
# Long/slow sweeps should be launched detached so a VPN drop cannot interrupt:
#   job run -t sweep "oam_exec.sh -o ~/evidence/x 'dwc_ep_ctl link_status'"
#
# Exit status is 0 even if some OAMs are silent — a silent console is DATA, not
# an error. The summary marks them, and `-- SILENT` is printed for each.
set -uo pipefail

BMC=${BMC:-}
OUT=""
OAMS="0 1 2 3 4 5 6 7"
SETTLE=5
TMO=40
PWFILE=${PILOT_BMC_PW:-$HOME/.pilot/bmc.pw}

while getopts ":b:o:d:w:t:h" o; do
	case "$o" in
	b) BMC=$OPTARG ;;
	o) OUT=$OPTARG ;;
	d) OAMS=$OPTARG ;;
	w) SETTLE=$OPTARG ;;
	t) TMO=$OPTARG ;;
	h)
		sed -n '2,22p' "$0"
		exit 0
		;;
	*)
		echo "oam_exec.sh: bad flag -$OPTARG" >&2
		exit 2
		;;
	esac
done
shift $((OPTIND - 1))

PRIVATE_ROOT=${COPILOT_PRIVATE_DIR:-$HOME/.copilot-private}
BMC_DEFAULT_FILE=$PRIVATE_ROOT/skills/machine-pilot/default-bmc
if [[ -z $BMC && -r $BMC_DEFAULT_FILE ]]; then
	IFS= read -r BMC <"$BMC_DEFAULT_FILE"
fi

[[ -n $BMC ]] || {
	echo "oam_exec.sh: BMC is missing; set -b BMC, BMC, or private station config" >&2
	exit 2
}

[[ $# -gt 0 ]] || {
	echo "oam_exec.sh: need at least one command (-h for help)" >&2
	exit 2
}
[[ -r $PWFILE ]] || {
	echo "oam_exec.sh: no BMC password at $PWFILE" >&2
	exit 2
}

[[ -n $OUT ]] || OUT="$HOME/.pilot/evidence/oam-exec/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT" || exit 2
SSHPASS=$(cat "$PWFILE")
export SSHPASS

printf '# oam_exec %s  bmc=%s  oams=[%s]\n' "$(date -u +%FT%TZ)" "$BMC" "$OAMS" >"$OUT/_meta.txt"
printf '# cmd: %s\n' "$@" >>"$OUT/_meta.txt"

silent=()
for n in $OAMS; do
	f="$OUT/oam$n.txt"
	{
		# The shell does not print a prompt unprompted; two CRs wake it.
		printf '\r'
		sleep 2
		printf '\r'
		sleep 1
		for c in "$@"; do
			printf '%s\r' "$c"
			sleep "$SETTLE"
		done
	} | timeout "$TMO" sshpass -e ssh -tt \
		-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
		-o LogLevel=ERROR -o ConnectTimeout=10 \
		-p "220$n" "root@$BMC" 2>&1 |
		sed 's/\x1b\[[0-9;]*m//g' | tr -d '\r' >"$f"

	sz=$(/usr/bin/wc -c <"$f" 2>/dev/null || echo 0)
	# A live shell echoes the command and replies; a wedged one yields ~nothing.
	if [[ $sz -lt 64 ]]; then
		silent+=("$n")
		printf 'oam%s: %sB  -- SILENT (wedged shell, or console held by another client)\n' "$n" "$sz"
	else
		printf 'oam%s: %sB\n' "$n" "$sz"
	fi
done

echo "out: $OUT"
if [[ ${#silent[@]} -gt 0 ]]; then
	echo "silent: ${silent[*]}"
	echo "hint: reset them by EXPLICIT index (-d all skips non-READY devices):"
	printf '      bmcjob run rst%s "bmccli device reset -d %s"\n' "${silent[0]}" "${silent[0]}"
fi
