# A Puppet Control Repository

## Install the agent
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
# on the puppet server:
sudo puppetserver ca sign --certname <name>
# do it again if you didn't wait two minutes
sudo /opt/puppetlabs/bin/puppet ssl bootstrap  
source /etc/profile.d/puppet-agent.sh
```

## Update Puppetfile
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

On the development client
```
scp post-receive vault:/opt/puppet-control-repo/puppet-control-repo.git/hooks
```

## Architecture

Here's a visual representation of the structure of this repository:

```
.
├── architecture.png                                        # an architecture diagram for the network
├── CODEOWNERS                                              # needed by github workers
├── data                                                    # hiera data
│   ├── common.yaml                                             # for all nodes
│   ├── nodes                                                   # for specific nodes, by hostname
│   │   ├── ipa.home.arpa.yaml
│   │   ├── nuc10i7fnh.home.arpa.yaml
│   │   ├── ognongle.home.arpa.yaml
│   │   ├── pihole.home.arpa.yaml
│   │   ├── vault.home.arpa.yaml
│   │   └── vm-debian.home.arpa.yaml
│   └── os                                                      # for specific nodes, by os family
│       ├── Debian.yaml
│       └── RedHat.yaml
├── environment.conf                                        # puppet environment configuration
├── hiera.yaml                                              # puppet hiera main configuration
├── LICENSE                                                 # LICENSE
├── manifests                                               # main manifest file
│   └── site.pp
├── modules                                                 # locally developed modules
│   ├── ca                                                      # internal certificate authority
│   ├── checkmk                                                 # docker checkmk custom module
│   ├── dns                                                     # DNS settings
│   ├── fail2ban_configuration                                  # remove fail2ban start/stop emails
│   ├── libvirt_log_fix                                         # remove libvirt log spam coming from a bug
│   ├── nfs                                                     # fix a startup bug of nfs mounts on debian
│   ├── nvidia-accounting-mode                                  # allow monitoring GPU memory usage on nvidia
│   ├── postfix_configuration                                   # configure postfix to send emails from all servers
│   ├── puppet_configuration                                    # configure puppet.conf
│   ├── tidy                                                    # wrapper around tidy to be able to specify paths in hiera
│   └── tor_relay                                               # configure a tor relay
├── post-receive                                            # post-receive hook that deploys the code to the puppetserver on every push
├── provisioning                                            # use ansible to provision virtual machines with libvirt
│   ├── README.md
│   ├── roles
│   │   ├── ansible-role-puppet-ca                              # custom role to deploy puppet to the new VMs
│   │   ├── ansible-role-virt-infra                             # role to deploy the VMs
│   │   └── requirements.yml
├── Puppetfile                                              # automatically generated with dependencies
├── Puppetfile-without-deps                                 # manually add dependencies to this
├── Rakefile
├── README.md                                               # this file
└── scripts                                                 # administration scripts
    ├── architecture.py                                         # generate the architecture diagram
    ├── autosign.py                                             # allow the puppet CA to autosign new VM requests, if they have a valid HashiCorp Vault token
    ├── config_version-r10k.rb
    ├── config_version-rugged.rb
    ├── config_version.sh
    ├── external_node_classifier.py                             # select dev/production environment according to the fully qualified domain name
    └── vault                                                   # vault policies and LDAP configuration
        ├── admin.hcl
        ├── ldap-config.sh
        └── puppet.hcl
```
