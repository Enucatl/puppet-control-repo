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
  }

  exec { 'sync-certs-and-generate-policy':
    command => "/bin/bash -c '
      # Create target dir if missing
      mkdir -p ${cert_target_dir}
  
      # Clean and copy each cert (removes the IPA headers)
      for f in ${source_dir}/*.crt; do
        [ -e \"\$f\" ] || continue
        filename=\$(basename \"\$f\")
        # This command extracts ONLY the certificate part
        openssl x509 -in \"\$f\" -out \"${cert_target_dir}/\$filename\"
      done
  
      # Build the JSON array of paths
      CERTS=\$(find ${cert_target_dir} -name \"*.crt\" -printf \"\\\"%p\\\",\" | sed \"s/,\$//\")
      
      # Write the policies.json
      echo \"{\\\"policies\\\": {\\\"Certificates\\\": {\\\"Install\\\": [\$CERTS]}}}\" > ${policy_dir}/policies.json
    '",
    path    => ['/bin', '/usr/bin'],
    # Run if source exists
    onlyif  => "/usr/bin/test -d ${source_dir}",
  }'

}
