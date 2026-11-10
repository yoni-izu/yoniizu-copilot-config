#!/usr/bin/env bash
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -r -- "$tmp"' EXIT
mkdir "$tmp/bin"
export CALLS="$tmp/calls"
cat >"$tmp/bin/mock" <<'MOCK'
#!/usr/bin/env bash
echo "$(basename "$0") $*" >>"$CALLS"
case "$(basename "$0")" in
    systemctl)
        [[ "${FAIL_MANAGER:-0}" != 1 || "$*" != *show-environment* ]] || exit 1
        [[ "${FAIL_ACTION:-0}" != 1 || "$*" != *restart* ]] || exit 1
        [[ "${INACTIVE:-0}" != 1 || "$*" != *is-active* ]] || exit 1
        ;;
    curl)
        [[ "${FAIL_HEALTH:-0}" != 1 ]] || exit 7
        echo "${HEALTH_BODY:-ok}"
        ;;
esac
MOCK
chmod +x "$tmp/bin/mock"
for command in systemctl curl journalctl sleep; do
    ln -s mock "$tmp/bin/$command"
done
export PATH="$tmp/bin:$PATH"

check() {
    local expected=$1 actual=0
    shift
    : >"$CALLS"
    bash "$root/portalctl" "$@" >"$tmp/out" 2>&1 || actual=$?
    if [[ "$actual" != "$expected" ]]; then
        cat "$tmp/out" >&2
        echo "Expected exit $expected, got $actual: $*" >&2
        exit 1
    fi
}

check 0 restart
grep -q 'systemctl --user restart task-dashboard.service' "$CALLS"
grep -q 'ready at http://127.0.0.1:8787' "$tmp/out"
check 0 start
check 0 stop
! grep -q '^curl' "$CALLS"
check 0 status
check 0 logs
grep -q 'journalctl --user -u task-dashboard.service -n 50 --no-pager' "$CALLS"
check 0 --help
[[ ! -s "$CALLS" ]]
check 2 invalid
check 2 restart extra
FAIL_MANAGER=1 check 1 restart
grep -q 'cannot reach the systemd user manager' "$tmp/out"
! grep -q 'systemctl --user restart' "$CALLS"
FAIL_ACTION=1 check 1 restart
! grep -q '^curl' "$CALLS"
FAIL_HEALTH=1 check 1 restart
grep -q 'readiness timed out' "$tmp/out"
HEALTH_BODY=unexpected check 1 restart
grep -q 'unexpected health response' "$tmp/out"
INACTIVE=1 check 1 restart
! grep -q '^curl' "$CALLS"
FAIL_HEALTH=1 check 7 status
echo "portalctl: 14 checks passed"
