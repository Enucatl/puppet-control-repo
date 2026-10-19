# Exercise real server-side Vault lookup without applying infrastructure profiles.
file { '/tmp/openvox-rehearsal-marker':
  ensure  => file,
  content => lookup('rehearsal::marker', String),
  mode    => '0644',
}
