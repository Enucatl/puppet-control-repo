#!/usr/bin/env bash

set -euo pipefail

sudo mkdir -p /opt/git
sudo chown user_l:user_l /opt/git
pushd /opt/git
git clone --bare https://github.com/Enucatl/puppet-control-repo.git
popd
sudo /opt/puppetlabs/puppet/bin/gem install r10k
sudo mkdir -p /etc/puppetlabs/r10k/
sudo cp puppet/config/r10k.yaml /etc/puppetlabs/r10k/r10k.yaml
sudo chown -R puppet:puppet /etc/puppetlabs/r10k
cp puppet/config/post-receive /opt/git/puppet-control-repo.git/hooks

sudo mkdir -p /var/cache/r10k
sudo chown -R puppet:puppet /var/cache/r10k
sudo chmod 755 /var/cache/r10k

sudo mkdir -p /etc/puppetlabs/code/environments
sudo chown -R puppet:puppet /etc/puppetlabs/code/environments
sudo chmod -R 755 /etc/puppetlabs/code/environments
