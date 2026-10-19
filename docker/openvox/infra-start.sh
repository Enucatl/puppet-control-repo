#!/usr/bin/env bash
set -euo pipefail

# Startup never initializes Vault, imports a CA, or replaces an absent identity.
for path in /trust/root.pem /trust/openvox-chain.pem \
  /etc/puppetlabs/puppetserver/ca/ca_crl.pem \
  /git/control.git/HEAD \
  /git/control.git/hooks/post-receive \
  /etc/puppetlabs/code/environments/production/.r10k-deploy.json \
  /etc/puppetlabs/code/environments/dev/.r10k-deploy.json; do
  [[ -r $path && -s $path ]] || {
    printf 'Restore established OpenVox state before startup; missing: %s\n' "$path" >&2
    exit 1
  }
done
cmp -s /infra/infra-host-post-receive /git/control.git/hooks/post-receive || {
  printf 'Install the managed host post-receive hook before startup\n' >&2
  exit 1
}
[[ -x /git/control.git/hooks/post-receive ]] || {
  printf 'Host post-receive hook must be executable\n' >&2
  exit 1
}
exec /usr/local/bin/openvox-server-entrypoint \
  /opt/puppetlabs/server/apps/puppetserver/cli/apps/foreground
