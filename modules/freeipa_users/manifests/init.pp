class freeipa_users (
  Hash $user_groups = {},
) {
  $user_groups.each |$username, $groups_array| {
    # Convert array ['docker', 'puppet'] to string "docker,puppet"
    $groups_list = join($groups_array, ',')

    exec { "add_ipa_user_${username}_to_groups":
      path    => ['/usr/bin', '/usr/sbin', '/bin'],
      # The command to add groups
      command => "usermod -a -G ${groups_list} ${username}",
      
      # SAFETY VALVE 1: Do not run if the user is already in these groups.
      # This prevents the command from running on every Puppet run.
      # We check if the output of 'id' contains the group names.
      unless  => "id -Gn ${username} | grep -qowE '${join($groups_array, '|')}'",

      # SAFETY VALVE 2: THE KEY FIX
      # Only attempt this if the user exists in the OS (via SSSD/IPA).
      # If 'id user' fails, Puppet does NOTHING (skips this resource).
      onlyif  => "id ${username}",
    }
  }
}
