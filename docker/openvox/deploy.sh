#!/usr/bin/env bash
set -euo pipefail

R10K=/opt/puppetlabs/puppet/bin/r10k
PUPPET=/opt/puppetlabs/bin/puppet
RUBY=/opt/puppetlabs/puppet/bin/ruby
CODE=/etc/puppetlabs/code
ENVIRONMENTS=$CODE/rehearsal-environments
STATE=/opt/puppetlabs/server/data/puppetserver/server_data
REPOSITORY=/git/control.git
CONFIG=/git/r10k.yaml

if [[ ${1:-} == --post-receive ]]; then
  trap 'printf "%s\n" "$?" > /git/deploy.status' EXIT
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR
  while read -r _old new ref; do
    case "$ref" in
      refs/heads/production|refs/heads/dev)
        [[ $new != 0000000000000000000000000000000000000000 ]] || continue
        environment=${ref#refs/heads/}
        "$R10K" deploy environment "$environment" --modules --config "$CONFIG"
        "$PUPPET" generate types --environment "$environment" \
          --config /etc/puppetlabs/puppet/puppet.conf \
          --codedir "$CODE" --environmentpath "$ENVIRONMENTS" --vardir "$STATE"
        ;;
    esac
  done

  # The initial deployment precedes server startup; later pushes flush its cache.
  "$RUBY" - <<'RUBY'
require 'net/http'
require 'openssl'
require 'socket'
host = 'openvox.rehearsal.test'
begin
  Socket.tcp(host, 8140, connect_timeout: 2).close
rescue SocketError, Errno::ECONNREFUSED, Errno::EHOSTUNREACH, Errno::ETIMEDOUT
  puts 'Server is not listening yet; environment cache invalidation skipped'
  exit
end
ssl = '/etc/puppetlabs/puppet/ssl'
http = Net::HTTP.new(host, 8140, nil)
http.use_ssl = true
http.verify_mode = OpenSSL::SSL::VERIFY_PEER
http.verify_hostname = true
http.min_version = OpenSSL::SSL::TLS1_3_VERSION
http.ca_file = '/trust/root.pem'
http.cert = OpenSSL::X509::Certificate.new(File.read("#{ssl}/certs/#{host}.pem"))
http.key = OpenSSL::PKey.read(File.read("#{ssl}/private_keys/#{host}.pem"))
http.open_timeout = 5
http.read_timeout = 10
%w[production dev].each do |environment|
  response = http.request(Net::HTTP::Delete.new("/puppet-admin-api/v1/environment-cache?environment=#{environment}"))
  abort "Environment cache invalidation returned HTTP #{response.code}" unless response.code == '204'
end
puts 'Server production/dev environment caches invalidated'
RUBY
  exit
fi

[[ $# == 0 ]] || { echo 'Usage: deploy.sh [--post-receive]' >&2; exit 2; }
mkdir -p "$ENVIRONMENTS" /var/cache/r10k
work=$(mktemp -d /git/rehearsal-deploy.XXXXXX)
trap 'rm -rf "$work"' EXIT
mkdir "$work/tree" "$work/template"

# Only committed files and these explicit overlays enter the disposable Git repo.
git -c safe.directory=/source -C /source archive HEAD | \
  tar -x -C "$work/tree" --exclude=.env
install -m 0644 /source/Puppetfile "$work/tree/Puppetfile"
install -m 0755 /source/scripts/autosign.py /source/scripts/external_node_classifier.py \
  "$work/tree/scripts/"
cp -R /rehearsal/fixture/. "$work/tree/"
"$RUBY" - <<'RUBY' "$work/tree/Puppetfile"
puppetfile = File.read(ARGV.fetch(0))
abort 'Rehearsal requires an immutable vault_secrets commit in Puppetfile' unless
  puppetfile.match?(/mod 'vault_secrets',\s+git: 'https:\/\/github\.com\/enucatl\/vault\.git',\s+commit: '[0-9a-f]{40}'/)
RUBY

if [[ ! -d $REPOSITORY ]]; then
  git init --bare --template="$work/template" "$REPOSITORY"
fi
mkdir -p "$REPOSITORY/hooks"
cat > "$REPOSITORY/hooks/post-receive" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail
/rehearsal/deploy.sh --post-receive
HOOK
chmod 0755 "$REPOSITORY/hooks/post-receive"
cat > "$CONFIG" <<'YAML'
:cachedir: '/var/cache/r10k'
:git:
  provider: shellgit
:sources:
  rehearsal:
    remote: '/git/control.git'
    basedir: '/etc/puppetlabs/code/rehearsal-environments'
YAML

git init --initial-branch=production --template="$work/template" "$work/tree"
git -C "$work/tree" add .
git -c user.name='OpenVox disposable rehearsal' -c user.email='rehearsal@example.invalid' \
  -C "$work/tree" commit --quiet -m 'Deploy sanitized control-repo rehearsal fixture'
git -C "$work/tree" branch dev
rm -f /git/deploy.status
git -C "$work/tree" push --force "$REPOSITORY" production dev

# Git reports successful receipt even when a post-receive hook fails.
[[ $(cat /git/deploy.status) == 0 ]]
for environment in production dev; do
  [[ -f $ENVIRONMENTS/$environment/.r10k-deploy.json ]]
  deployed=$("$RUBY" -rjson -e 'puts JSON.parse(File.read(ARGV.fetch(0))).fetch("signature")' \
    "$ENVIRONMENTS/$environment/.r10k-deploy.json")
  [[ $deployed == "$(git -C "$work/tree" rev-parse HEAD)" ]]
done
