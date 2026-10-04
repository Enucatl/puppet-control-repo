#!/usr/bin/env bash
set -euo pipefail

# Run on the Puppet Server host. Keep its existing heap and other settings.
config=/etc/puppetlabs/puppetserver/conf.d/puppetserver.conf
sudo cp --update=none --preserve=all "$config" "$config.before-homelab-tuning"
sudo sed -i '/^# BEGIN homelab JRuby tuning$/,/^# END homelab JRuby tuning$/d' "$config"
# HOCON resolves these trailing assignments over earlier values in this file.
sudo tee -a "$config" >/dev/null <<'EOF'
# BEGIN homelab JRuby tuning
jruby-puppet.max-active-instances = 1
jruby-puppet.max-requests-per-instance = 10000
# END homelab JRuby tuning
EOF
echo 'Configuration written. Apply with: sudo systemctl restart puppetserver'
