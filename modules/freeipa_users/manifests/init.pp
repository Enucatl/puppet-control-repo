class freeipa_users (
  Hash $users       = {},
  Hash $user_groups = {},
) {
  # Create FreeIPA users that don't yet exist.
  # Host principals cannot create users, so this authenticates as the IPA
  # admin. The password is written to a root-only tmpfs file so it never
  # appears in the Puppet catalog, PuppetDB reports, or agent logs.
  if $users != {} {
    $admin_password = Sensitive(lookup('freeipa::install::server::options::admin-password'))

    file { '/run/puppet-ipa-admin-pass':
      ensure  => file,
      content => $admin_password,
      owner   => 'root',
      group   => 'root',
      mode    => '0400',
    }

    $users.each |$username, $attrs| {
      $first = $attrs['first']
      $last  = $attrs['last']
      $shell = $attrs.get('shell', '/usr/sbin/nologin')

      exec { "ipa-user-add-${username}":
        command => "bash -c 'kinit admin < /run/puppet-ipa-admin-pass && ipa user-add ${username} --first=${first} --last=${last} --shell=${shell}'",
        unless  => "bash -c 'kinit admin < /run/puppet-ipa-admin-pass && ipa user-show ${username}'",
        path    => ['/usr/bin', '/usr/sbin', '/bin'],
        require => File['/run/puppet-ipa-admin-pass'],
      }
    }
  }

  # Add existing IPA users to local groups (e.g. docker).
  # Skipped if the user doesn't exist locally yet (SSSD may not have synced).
  $user_groups.each |$username, $groups_array| {
    $groups_list = join($groups_array, ',')

    exec { "add_ipa_user_${username}_to_groups":
      path    => ['/usr/bin', '/usr/sbin', '/bin'],
      command => "usermod -a -G ${groups_list} ${username}",
      unless  => "id -Gn ${username} | grep -qowE '${join($groups_array, '|')}'",
      onlyif  => "id ${username}",
    }
  }
}
