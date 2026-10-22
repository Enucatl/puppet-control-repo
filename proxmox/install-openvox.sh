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
release=https://github.com/Enucatl/openvox-packages/releases/download/resolute-28001
version="9.1.1+homelab.28001-1+$target"
packages=("openvox-agent=$version")
native_release=https://github.com/Enucatl/openvox-packages/releases/download/native-client-2-1
case "$target" in
  ubuntu26.04)
    packages+=(curl=8.18.0-1ubuntu2.8+homelab.1+ubuntu26.04
      libcurl4t64=8.18.0-1ubuntu2.8+homelab.1+ubuntu26.04
      python3-ipalib=4.13.1-1+homelab.1+ubuntu26.04)
    ;;
  debian13)
    packages+=(curl=8.14.1-2+deb13u7+homelab.1+debian13
      libcurl4t64=8.14.1-2+deb13u7+homelab.1+debian13
      python3-ipalib=4.12.4-1+homelab.1+debian13)
    ;;
esac
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
if (( ${#packages[@]} > 1 )); then
  curl --fail --silent --show-error --location "$native_release/native-client-$target.pref" --output "$work/native-preferences"
  install -m 0644 "$work/native-preferences" /etc/apt/preferences.d/openvox-native-client
  printf 'deb [arch=amd64 signed-by=/etc/apt/keyrings/openvox-archive-keyring.asc] %s/ ./\n' "$native_release" \
    > /etc/apt/sources.list.d/openvox-native-client.list
fi
apt-get update
# No downgrade override: a newer installed security revision requires review.
apt-get install -y "${packages[@]}"
for specification in "${packages[@]}"; do
  package=${specification%%=*}
  [[ $(dpkg-query -W -f='${Version}' "$package") == "${specification#*=}" ]] || {
    echo "Installed $package version differs from accepted package." >&2; exit 1;
  }
done
# Remove the compatibility policy only after the native package is verified.
rm -f /etc/systemd/system/puppet.service.d/puppet-crypto.conf \
  /usr/local/libexec/openvox-child-policy.rb /etc/puppetlabs/puppet/openssl.cnf
/opt/puppetlabs/bin/puppet config set key_type mldsa44 --section main
/opt/puppetlabs/bin/puppet config set key_type mldsa44 --section agent
[[ $(/opt/puppetlabs/bin/puppet config print key_type --section agent) == mldsa44 ]]
/opt/puppetlabs/puppet/bin/ruby -rpuppet -rtmpdir -e '
  abort "OpenSSL 3.5+ required" if OpenSSL::OPENSSL_VERSION_NUMBER < 0x30500000
  key = OpenSSL::PKey.generate_key("ML-DSA-44")
  abort "ML-DSA signing failed" unless key.verify(nil, key.sign(nil, "installation-check"), "installation-check")
  Puppet.initialize_settings
  Dir.mktmpdir do |directory|
    provider = Puppet::X509::CertProvider.new(privatekeydir: directory, hostprivkey: nil)
    provider.save_private_key("installation-check", key)
    encoded = File.read(File.join(directory, "installation-check.pem")).lines[1..-2].join.unpack1("m")
    payload = OpenSSL::ASN1.decode(OpenSSL::ASN1.decode(encoded).value[2].value)
    abort "ML-DSA seed-only writer required" unless payload.tag == 0 && payload.tag_class == :CONTEXT_SPECIFIC && payload.value.bytesize == 32
  end
'
systemctl daemon-reload
echo "Installed accepted OpenVox $version; service remains masked pending authenticated enrollment."
