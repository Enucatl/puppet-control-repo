# A Puppet Control Repository

Install puppet agent following
https://puppet.com/docs/puppet/8/install_agents.html#install_agents

Download package from http://apt.puppet.com/
```
sudo dpkg -i package
sudo apt update
sudo apt install puppet-agent
sudo /opt/puppetlabs/bin/puppet config set server vault.home.arpa --section main
sudo /opt/puppetlabs/bin/puppet resource service puppet ensure=running enable=true
sudo /opt/puppetlabs/bin/puppet ssl bootstrap
sudo puppetserver ca sign --certname <name>
# do it again if you didn't wait two minutes
sudo /opt/puppetlabs/bin/puppet ssl bootstrap  
source /etc/profile.d/puppet-agent.sh
```

Resolve dependencies
```
generate-puppetfile -p Puppetfile-without-deps
```

Apply the latest definition on the puppet server
```
sudo -u puppet r10k deploy environment --modules -v info \
&& sudo -u puppet /opt/puppetlabs/puppet/bin/puppet generate types \
--environment production \
--confdir /etc/puppetlabs/puppet \
--codedir /etc/puppetlabs/code \
--vardir /opt/puppetlabs/puppet/cache
```

The post-receive hook does the above automatically

On the server
```
cd /opt
sudo mkdir puppet-control-repo
sudo chown -R $USER:$USER !$
cd !$
git clone --bare https://github.com/Enucatl/puppet-control-repo.git
```

On the client
```
scp post-receive vault:/opt/puppet-control-repo/puppet-control-repo.git/hooks
```

## What You Get From This control-repo

Here's a visual representation of the structure of this repository:

```
control-repo/
├── data/                                 # Hiera data directory.
│   ├── nodes/                            # Node-specific data goes here.
│   └── common.yaml                       # Common data goes here.
├── manifests/
│   └── site.pp                           # The "main" manifest that contains a default node definition.
├── scripts/
│   ├── code_manager_config_version.rb    # A config_version script for Code Manager.
│   ├── config_version.rb                 # A config_version script for r10k.
│   └── config_version.sh                 # A wrapper that chooses the appropriate config_version script.
├── modules/                              # Locally developed modules
├── LICENSE
├── Puppetfile                            # A list of external Puppet modules to deploy with an environment.
├── README.md
├── environment.conf                      # Environment-specific settings. Configures the modulepath and config_version.
└── hiera.yaml                            # Hiera's configuration file. The Hiera hierarchy is defined here.
```
