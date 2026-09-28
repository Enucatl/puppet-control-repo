class freeipa_users (
  Hash $user_groups = {},
) {
  file { '/run/puppet-ipa-admin-pass':
    ensure => absent,
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
