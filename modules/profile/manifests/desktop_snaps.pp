# Desktop applications supplied by their stable Snap channels.
class profile::desktop_snaps {
  include snapd

  # Snapd handles background refreshes; installed avoids Store queries during catalogs.
  package { ['telegram-desktop', 'moonlight', 'chromium']:
    ensure          => installed,
    provider        => snap,
    install_options => ['--channel=latest/stable'],
    require         => Service['snapd'],
  }

  exec { 'moonlight-joystick':
    provider => shell,
    command => '/usr/bin/snap connect moonlight:joystick :joystick',
    unless  => "/usr/bin/snap connections moonlight | /usr/bin/grep -Eq '^joystick[[:space:]]+moonlight:joystick[[:space:]]+:joystick[[:space:]]'",
    require => Package['moonlight'],
  }
}
