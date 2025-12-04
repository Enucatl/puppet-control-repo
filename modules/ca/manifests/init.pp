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
class ca (
  String $vault_addr = 'https://127.0.0.1:8200',
) {

  include ::ca_cert

  # 2. Create a staging directory to store the raw downloads
  $staging_dir = '/opt/vault_cert_staging'

  file { $staging_dir:
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0700',
  }

  # --- Root CA Section ---

  # Download the Root CA to the staging folder
  exec { 'download_vault_root_ca':
    command => "/usr/bin/curl --insecure -s ${vault_addr}/v1/pki/ca/pem -o ${staging_dir}/vault_root.crt",
    creates => "${staging_dir}/vault_root.crt", # Only download if it doesn't exist
    require => [File[$staging_dir], Package['curl']],
  }

  # Install the Root CA using pcfens/ca_cert
  ca_cert { 'vault_root':
    ensure  => trusted,
    source  => "file://${staging_dir}/vault_root.crt",
    require => Exec['download_vault_root_ca'],
  }

  # --- Intermediate CA Section ---

  # Download the Intermediate CA to the staging folder
  exec { 'download_vault_intermediate_ca':
    command => "/usr/bin/curl --insecure -s ${vault_addr}/v1/pki_int/ca/pem -o ${staging_dir}/vault_intermediate.crt",
    creates => "${staging_dir}/vault_intermediate.crt",
    require => [File[$staging_dir], Package['curl']],
  }

  # Install the Intermediate CA using pcfens/ca_cert
  ca_cert { 'vault_intermediate':
    ensure  => trusted,
    source  => "file://${staging_dir}/vault_intermediate.crt",
    require => Exec['download_vault_intermediate_ca'],
  }

  # Ensure Curl is present
  if ! defined(Package['curl']) {
    package { 'curl': ensure => present }
  }
}
