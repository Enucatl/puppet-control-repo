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

  exec { 'chromium-system-policies':
    provider => shell,
    command  => '/usr/bin/snap connect chromium:etc-chromium-browser-policies :system-files',
    unless   => "/usr/bin/snap connections chromium | /usr/bin/grep -Eq '^system-files[[:space:]]+chromium:etc-chromium-browser-policies[[:space:]]+:system-files[[:space:]]'",
    require  => Package['chromium'],
  }

  exec { 'moonlight-joystick':
    provider => shell,
    command => '/usr/bin/snap connect moonlight:joystick :joystick',
    unless  => "/usr/bin/snap connections moonlight | /usr/bin/grep -Eq '^joystick[[:space:]]+moonlight:joystick[[:space:]]+:joystick[[:space:]]'",
    require => Package['moonlight'],
  }
}
