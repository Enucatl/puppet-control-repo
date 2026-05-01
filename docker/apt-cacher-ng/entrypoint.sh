#!/bin/sh
set -eu

log() {
  printf '%s\n' "$*" >&2
}

maintain_cache() {
  log "[apt-cacher-ng] maintenance: start"
  if /usr/lib/apt-cacher-ng/acngtool maint -c /etc/apt-cacher-ng \
    SocketPath=/var/run/apt-cacher-ng/socket >/dev/null 2>&1
  then
    log "[apt-cacher-ng] maintenance: done"
  else
    log "[apt-cacher-ng] maintenance: failed"
  fi
}

maint_loop() {
  sleep 60
  while true; do
    maintain_cache
    sleep 86400
  done
}

cleanup() {
  if [ -n "${maint_pid:-}" ]; then
    kill "$maint_pid" 2>/dev/null || true
    wait "$maint_pid" 2>/dev/null || true
  fi
  if [ -n "${daemon_pid:-}" ]; then
    kill "$daemon_pid" 2>/dev/null || true
    wait "$daemon_pid" 2>/dev/null || true
  fi
}

trap cleanup INT TERM

log "[apt-cacher-ng] wrapper: starting daemon and maintenance loop"

maint_loop &
maint_pid=$!

"$@" &
daemon_pid=$!

log "[apt-cacher-ng] wrapper: daemon pid=$daemon_pid maintenance pid=$maint_pid"

set +e
wait "$daemon_pid"
status=$?
set -e
log "[apt-cacher-ng] wrapper: daemon exited status=$status"
cleanup
exit "$status"
