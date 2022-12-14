# A Puppet Control Repository

Install puppet agent following
https://puppet.com/docs/puppet/7/install_agents.html#install_agents

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
sudo r10k deploy environment --modules -v info
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
├── site-modules/                         # This directory contains site-specific modules and is added to $modulepath.
│   ├── profile/                          # The profile module.
│   └── role/                             # The role module.
├── LICENSE
├── Puppetfile                            # A list of external Puppet modules to deploy with an environment.
├── README.md
├── environment.conf                      # Environment-specific settings. Configures the modulepath and config_version.
└── hiera.yaml                            # Hiera's configuration file. The Hiera hierarchy is defined here.
```
