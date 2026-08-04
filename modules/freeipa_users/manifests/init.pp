class freeipa_users (
  Sensitive[String] $admin_password,
  Hash              $users       = {},
  Hash              $user_groups = {},
) {
  # Create FreeIPA users that don't yet exist.
  # Host principals cannot create users, so this authenticates as the IPA
  # admin. The password is written to a root-only tmpfs file so it never
  # appears in the Puppet catalog, PuppetDB reports, or agent logs.
  if $users != {} {
    file { '/run/puppet-ipa-admin-pass':
      ensure  => file,
      content => $admin_password,
      owner   => 'root',
      group   => 'root',
      mode    => '0400',
    }

    $users.each |$username, $attrs| {
      $first  = $attrs['first']
      $last   = $attrs['last']
      $shell  = $attrs.get('shell', '/usr/sbin/nologin')
      $keytab = $attrs.get('keytab', undef)

      exec { "ipa-user-add-${username}":
        command => "bash -c 'kinit admin < /run/puppet-ipa-admin-pass && ipa user-add ${username} --first=${first} --last=${last} --shell=${shell}'",
        unless  => "bash -c 'kinit admin < /run/puppet-ipa-admin-pass && ipa user-show ${username}'",
        path    => ['/usr/bin', '/usr/sbin', '/bin'],
        require => File['/run/puppet-ipa-admin-pass'],
      }

      if $keytab {
        exec { "ipa-getkeytab-${username}":
          command => "bash -c 'kinit admin < /run/puppet-ipa-admin-pass && ipa-getkeytab -s freeipa.home.arpa -p ${username} -k ${keytab}'",
          unless  => "bash -c 'test -s ${keytab} && klist -k ${keytab} | grep -qF ${username}@'",
          path    => ['/usr/bin', '/usr/sbin', '/bin'],
          require => Exec["ipa-user-add-${username}"],
        }

        file { $keytab:
          ensure  => file,
          owner   => 'root',
          group   => 'root',
          mode    => '0400',
          require => Exec["ipa-getkeytab-${username}"],
        }
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
