#!/usr/bin/env bash
set -euo pipefail
repo=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export TEST_WORK=$work
mkdir "$work/bin"
cat > "$work/bin/sudo" <<'MOCK'
#!/usr/bin/env bash
set -euo pipefail
args=()
for arg in "$@"; do
    args+=("${arg//\/etc\/puppetlabs\/puppetserver\/conf.d\//$TEST_WORK/}")
done
case "${args[0]}" in cp|sed|tee) exec "${args[@]}" ;; *) exit 99 ;; esac
MOCK
chmod +x "$work/bin/sudo"
export PATH="$work/bin:$PATH"
cat > "$work/puppetserver.conf" <<'CONFIG'
jruby-puppet: {
    ruby-load-path: ["/keep/this/path"]
    max-active-instances: 4
    max-requests-per-instance: 0
}
CONFIG
cp "$work/puppetserver.conf" "$work/original"
bash "$repo/scripts/configure-puppetserver.sh"
cp "$work/puppetserver.conf" "$work/first"
bash "$repo/scripts/configure-puppetserver.sh"
cmp "$work/original" "$work/puppetserver.conf.before-homelab-tuning"
cmp "$work/first" "$work/puppetserver.conf"
test "$(rg -c '^# BEGIN homelab JRuby tuning$' "$work/puppetserver.conf")" = 1
rg -q 'ruby-load-path: \["/keep/this/path"\]' "$work/puppetserver.conf"
rg -q '^jruby-puppet.max-active-instances = 1$' "$work/puppetserver.conf"
rg -q '^jruby-puppet.max-requests-per-instance = 10000$' "$work/puppetserver.conf"

# Optional semantic check using the installed server's actual HOCON parser.
jar=/opt/puppetlabs/server/apps/puppetserver/puppet-server-release.jar
if [ -r "$jar" ]; then
    cat > "$work/Check.java" <<'JAVA'
import com.typesafe.config.ConfigFactory;
import java.io.File;
class Check {
    public static void main(String[] args) {
        var config = ConfigFactory.parseFile(new File(args[0])).resolve();
        if (config.getInt("jruby-puppet.max-active-instances") != 1
            || config.getInt("jruby-puppet.max-requests-per-instance") != 10000
            || !config.getStringList("jruby-puppet.ruby-load-path").get(0).equals("/keep/this/path"))
            throw new AssertionError("JRuby settings or preserved configuration differ");
    }
}
JAVA
    java --class-path "$jar" "$work/Check.java" "$work/puppetserver.conf"
fi
echo 'Puppet Server configuration checks passed.'
