#!/usr/bin/env bash
# uart_capture.sh — bounded, read-only UART capture. Runs ON the box that owns the
# serial adapter (bench host or supplementary NUC):
#   ssh user@host bash -s -- [port] [seconds] [baud] < uart_capture.sh > uart.log
# Defaults: /dev/ttyACM0, 60 s, 115200 8N1 raw. Capture goes to STDOUT (redirect it);
# diagnostics go to STDERR. Refuses to run if another process holds the port.
# Does not send anything to the device.
set -u
port=${1:-/dev/ttyACM0}
secs=${2:-60}
baud=${3:-115200}

err() { printf 'uart_capture: %s\n' "$*" >&2; }

[[ -e "$port" ]] || { err "no such port: $port (have: $(ls /dev/ttyACM* /dev/ttyUSB* 2>/dev/null | tr '\n' ' '))"; exit 2; }
[[ -r "$port" ]] || { err "not readable: $port ($(ls -l "$port")); groups=$(id -nG)"; exit 2; }

byid=$(ls -l /dev/serial/by-id/ 2>/dev/null | awk -v p="$(basename "$port")" '$NF ~ p"$" {print $9}' | tr '\n' ' ')
err "port=$port adapter=${byid:-unknown} baud=$baud secs=$secs host=$(hostname)"

base=$(basename "$port")
for lk in "/var/lock/LCK..$base" "/run/lock/LCK..$base"; do
  if [[ -e $lk ]]; then
    pid=$(tr -dc '0-9' < "$lk" | head -c 10)
    if [[ -n $pid ]] && kill -0 "$pid" 2>/dev/null; then
      err "port locked by pid $pid ($(ps -o comm= -p "$pid" 2>/dev/null)) via $lk — refusing; ask the owner"
      exit 3
    fi
    err "stale lock $lk (pid ${pid:-?} gone) — ignoring"
  fi
done
holders=$(pgrep -a -E 'minicom|screen|picocom|socat|tio|cat ' 2>/dev/null | grep -F -- "$port")
if [[ -n $holders ]]; then err "port already read by: $holders — refusing (one reader per port)"; exit 3; fi
if command -v fuser >/dev/null 2>&1 && fuser -s "$port" 2>/dev/null; then
  err "port busy per fuser: $(fuser -v "$port" 2>&1 | tail -n +2 | tr -s ' ' | tr '\n' ';') — refusing"; exit 3
fi

if ! stty -F "$port" "$baud" cs8 -cstopb -parenb -ixon -ixoff -crtscts raw -echo 2>/dev/null; then
  err "stty failed on $port (adapter unplugged? permissions?)"; exit 4
fi
err "capturing… (started $(date -u +%H:%M:%SZ); trigger the event now if you want its banner)"
timeout "$secs" cat "$port"
rc=$?
# 124 = timeout elapsed = normal end of a bounded capture
[[ $rc -eq 124 || $rc -eq 0 ]] || err "cat exited rc=$rc"
err "done $(date -u +%H:%M:%SZ)"
exit 0
