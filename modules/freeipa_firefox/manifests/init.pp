class freeipa_firefox {

  $source_dir = '/usr/local/share/ca-certificates/ipa-ca'
  $policy_dir = '/etc/firefox/policies'
  $cert_target_dir = "${policy_dir}/certs"

  # 1. Create the directories
  file { [ '/etc/firefox', $policy_dir ]:
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
  }

  # 2. Sync the certs from /usr/local/share to /etc/firefox
  # Using 'file' with 'source' and 'recurse' is better than 'cp' 
  # because it handles deletions and permissions automatically.
  file { $cert_target_dir:
    ensure  => directory,
    source  => "file://${source_dir}",
    recurse => true,
    purge   => true,
    force   => true,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    notify  => Exec['generate-firefox-policies'],
    require => File[$cert_target_dir],
  }

  # 3. The Exec that builds the policies.json
  # This script finds all .crt files, escapes them for JSON, and writes the file.
  exec { 'generate-firefox-policies':
    command     => "/bin/bash -c '
      CERTS=\$(find ${cert_target_dir} -name \"*.crt\" -printf \"\\\"%p\\\",\" | sed \"s/,\$//\")
      echo \"{\\\"policies\\\": {\\\"Certificates\\\": {\\\"Install\\\": [\$CERTS]}}}\" > ${policy_dir}/policies.json
    '",
    refreshonly => true,
    subscribe   => File[$cert_target_dir],
  }
}
