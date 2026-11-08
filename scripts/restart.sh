#!/usr/bin/env bash
# Restart the TR-8S studio safely.
#
# Two hard-won rules are baked in:
#   1. Never `pkill -f tr8s.server` -- the pattern matches this very shell and
#      kills your terminal. Kill the old studio by the PID we wrote down.
#   2. Wait until the old process has really exited, and on Linux until the
#      MIDI port is free, before starting the new one. Two readers on one
#      rawmidi node split the SysEx stream and the machine's replies come back
#      garbled ("pppp777", "p7dp"). On macOS the wait is for the exit itself:
#      a CoreMIDI client that is still closing its port can hang.
#
# Usage:  scripts/restart.sh [--slot 8-16] [--port 8733] [--offline]
#         With no --slot the studio resumes on the slot the machine was last on.
# Env:    TR8S_PORT  forces the MIDI port: a node (/dev/snd/midiCxDy) on Linux,
#         a CoreMIDI port name (TR8S_PORT="TR-8S") on macOS.
set -u
cd "$(dirname "$0")/.."
RUN=.run; mkdir -p "$RUN"
PID=$RUN/studio.pid; LOG=$RUN/studio.log
# The fuser wait only makes sense for a /dev/snd node, not a CoreMIDI name.
PORT_NODE=${TR8S_PORT:-$(ls /dev/snd/midiC*D* 2>/dev/null | head -1)}
case "${PORT_NODE:-}" in /dev/snd/*) ;; *) PORT_NODE= ;; esac

if [ -f "$PID" ]; then
  OLD=$(cat "$PID")
  if kill "$OLD" 2>/dev/null; then
    # wait for it to exit, ~10 s at most (kill -0 sends nothing, it only asks)
    for _ in $(seq 1 40); do
      kill -0 "$OLD" 2>/dev/null || break
      sleep 0.25
    done
    if kill -0 "$OLD" 2>/dev/null; then
      echo "old studio (pid $OLD) did not exit"; exit 1
    fi
  fi
fi
if [ -n "${PORT_NODE:-}" ] && command -v fuser >/dev/null 2>&1; then
  for _ in $(seq 1 40); do
    fuser "$PORT_NODE" >/dev/null 2>&1 || break
    sleep 0.25
  done
  if fuser "$PORT_NODE" >/dev/null 2>&1; then
    echo "MIDI port still held by:"; fuser -v "$PORT_NODE"; exit 1
  fi
fi

PYTHON=${PYTHON:-$([ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)}
PYTHONPATH=src nohup "$PYTHON" -m tr8s.server "$@" > "$LOG" 2>&1 &
echo $! > "$PID"
for _ in $(seq 1 60); do
  grep -q "tr8s studio ->" "$LOG" 2>/dev/null && break
  sleep 0.25
done
grep -E "device:|tr8s studio ->" "$LOG" | head -3
