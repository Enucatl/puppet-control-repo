class freeipa_firefox {

  $source_dir      = '/usr/local/share/ca-certificates/ipa-ca'
  $policy_dir      = '/etc/firefox/policies'
  $cert_target_dir = "${policy_dir}/certs"

  # 1. Create the directories
  file { [ '/etc/firefox', $policy_dir, $cert_target_dir ]:
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
  }

  # 2. Watch the source directory for changes.
  # We don't sync files directly here because the 'exec' needs to process them.
  # This 'file' resource acts as a trigger.
  file { 'ipa-cert-trigger':
    ensure  => directory,
    path    => $source_dir,
    recurse => true,
    notify  => Exec['generate-firefox-policies'],
  }

  # 3. Handle the JSON file existence.
  # If the policies.json is deleted, this ensures the exec runs again.
  file { "${policy_dir}/policies.json":
    ensure => present,
    replace => false, # Don't overwrite content, just ensure it exists
    notify  => Exec['generate-firefox-policies'],
  }

  # 4. The Execution logic
  exec { 'generate-firefox-policies':
    command     => "/bin/bash -c '
      # 1. Clean out the target cert dir to ensure it matches source exactly
      rm -f ${cert_target_dir}/*.crt

      # 2. Extract and clean certificates
      for f in ${source_dir}/*.crt; do
        [ -e \"\$f\" ] || continue
        filename=\$(basename \"\$f\")
        openssl x509 -in \"\$f\" -out \"${cert_target_dir}/\$filename\"
      done

      # 3. Build the JSON array
      CERTS=\$(find ${cert_target_dir} -name \"*.crt\" -printf \"\\\"%p\\\",\" | sed \"s/,\$//\")
      
      # 4. Atomically write the policies.json
      echo \"{\\\"policies\\\": {\\\"Certificates\\\": {\\\"Install\\\": [\$CERTS]}}}\" > ${policy_dir}/policies.json.tmp
      mv ${policy_dir}/policies.json.tmp ${policy_dir}/policies.json
    '",
    path        => ['/bin', '/usr/bin'],
    refreshonly => true, # ONLY runs when a cert changes or the JSON is missing
    require     => File[$cert_target_dir],
  }
}
