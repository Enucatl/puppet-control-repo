#!/usr/bin/env sh
set -eu

if [ -f /run/secrets/wolf_dropbear_key ]; then
  install -m 0700 -d /run/wolf
  install -m 0600 /run/secrets/wolf_dropbear_key /run/wolf/dropbear_key
fi

exec "$@"
