# Class: ca
#
# This class manages the Certificate Authority (CA) configuration.
# It installs and configures CA certificates.
#
# Parameters:
#   None
#
# Example Usage:
#   class { 'ca': }
#
class ca {

  # ca_cert::ca - Define a trusted CA certificate for the Puppet infrastructure.
  #
  # Parameters:
  #   - name: The name of the CA certificate.
  #   - ensure: Specifies the desired state of the CA certificate (default: 'trusted').
  #   - source: The source URL for the CA certificate file.
  #
  ca_cert::ca { 'puppet_ca':
    ensure => 'trusted',
    source => 'puppet:///modules/ca/puppet-ca.crt',
  }

  # ca_cert::ca - Define a trusted CA certificate for the root CA in 2022.
  #
  # Parameters:
  #   - name: The name of the CA certificate.
  #   - ensure: Specifies the desired state of the CA certificate (default: 'trusted').
  #   - source: The source URL for the CA certificate file.
  #
  ca_cert::ca { 'root_2022_ca':
    ensure => 'trusted',
    source => 'puppet:///modules/ca/root_2022_ca.crt',
  }

  # file - Manage the SSH CA public key file.
  #
  # Parameters:
  #   - path: The path to the SSH CA public key file.
  #   - source: The source URL for the SSH CA public key file.
  #   - owner: The owner of the file (default: 'root').
  #   - group: The group of the file (default: 'root').
  #   - mode: The file permission mode (default: '0644').
  #
  file { '/etc/ssh/ssh_ca.pub':
    source => 'puppet:///modules/ca/ssh_ca.pub',
    owner  => 'root',
    group  => 'root',
    mode   => '0644',
    before => Class['ssh'],
  }

}
