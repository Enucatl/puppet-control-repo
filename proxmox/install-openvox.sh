#!/usr/bin/env bash
set -euo pipefail

# Install only the coordinated, accepted OS/architecture pair before enrollment.
[[ $(id -u) == 0 ]] || { echo 'Run as root.' >&2; exit 1; }
. /etc/os-release
case "$ID:$VERSION_ID:$(dpkg --print-architecture)" in
  ubuntu:24.04:amd64|ubuntu:26.04:amd64) target="ubuntu$VERSION_ID" ;;
  debian:13:amd64) target=debian13 ;;
  *) echo 'No accepted OpenVox package for this OS/architecture; refusing installation.' >&2; exit 1 ;;
esac
release=https://github.com/Enucatl/openvox-packages/releases/download/resolute-22001
version="9.1.0+homelab.22001-1+$target"
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
systemctl mask --runtime --now puppet.service
apt-get update
apt-get install -y ca-certificates curl gnupg
curl --fail --silent --show-error --location \
  https://raw.githubusercontent.com/Enucatl/openvox-packages/4f719cb678cc06a6f8c57a2e4a6a6915b84413c9/openvox-archive-keyring.asc \
  --output "$work/key.asc"
fingerprints=$(gpg --batch --show-keys --with-colons "$work/key.asc" |
  awk -F: '$1 == "pub" { primary = 1; next } primary && $1 == "fpr" { print $10; primary = 0 }')
[[ $fingerprints == 775661C709ACE6F7C1CF0FF6C4B7528F03E0BB94 ]] || {
  echo 'OpenVox archive fingerprint mismatch; refusing repository.' >&2; exit 1;
}
curl --fail --silent --show-error --location "$release/$target.pref" --output "$work/preferences"
install -d -m 0755 /etc/apt/keyrings /etc/apt/preferences.d
install -m 0644 "$work/key.asc" /etc/apt/keyrings/openvox-archive-keyring.asc
install -m 0644 "$work/preferences" /etc/apt/preferences.d/openvox-homelab
printf 'deb [arch=amd64 signed-by=/etc/apt/keyrings/openvox-archive-keyring.asc] %s/ ./\n' "$release" \
  > /etc/apt/sources.list.d/openvox-homelab.list
apt-get update
apt-get install -y "openvox-agent=$version"
[[ $(dpkg-query -W -f='${Version}' openvox-agent) == "$version" ]] || {
  echo 'Installed OpenVox version differs from accepted package.' >&2; exit 1;
}
install -d -m 0755 /etc/puppetlabs/puppet /etc/systemd/system/puppet.service.d
cat > /etc/puppetlabs/puppet/openssl.cnf <<'POLICY'
config_diagnostics = 1
openssl_conf = openvox_init
[openvox_init]
providers = openvox_providers
ssl_conf = openvox_ssl
[openvox_providers]
default = openvox_default
base = openvox_base
[openvox_default]
activate = 1
ml-dsa = openvox_mldsa
[openvox_base]
activate = 1
ml-dsa = openvox_mldsa
[openvox_mldsa]
retain_seed = yes
prefer_seed = yes
output_formats = seed-only
[openvox_ssl]
system_default = openvox_tls
[openvox_tls]
MinProtocol = TLSv1.3
MaxProtocol = TLSv1.3
Protocol = -ALL,+TLSv1.3
Groups = X25519MLKEM768
POLICY
cat > /etc/systemd/system/puppet.service.d/crypto.conf <<'POLICY'
[Service]
Environment=OPENSSL_CONF=/etc/puppetlabs/puppet/openssl.cnf
POLICY
export OPENSSL_CONF=/etc/puppetlabs/puppet/openssl.cnf
/opt/puppetlabs/bin/puppet config set key_type mldsa44 --section main
/opt/puppetlabs/bin/puppet config set key_type mldsa44 --section agent
[[ $(/opt/puppetlabs/bin/puppet config print key_type --section agent) == mldsa44 ]]
/opt/puppetlabs/puppet/bin/ruby -ropenssl -e '
  abort "OpenSSL 3.5+ required" if OpenSSL::OPENSSL_VERSION_NUMBER < 0x30500000
  key = OpenSSL::PKey.generate_key("ML-DSA-44")
  abort "ML-DSA signing failed" unless key.verify(nil, key.sign(nil, "installation-check"), "installation-check")
  payload = OpenSSL::ASN1.decode(OpenSSL::ASN1.decode(key.private_to_der).value[2].value)
  abort "ML-DSA seed-only writer required" unless payload.tag == 0 && payload.tag_class == :CONTEXT_SPECIFIC && payload.value.bytesize == 32
'
systemctl daemon-reload
echo "Installed accepted OpenVox $version; service remains masked pending authenticated enrollment."
