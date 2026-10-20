class profile::desktop_archives (
  Pattern[/\A[0-9]+\.[0-9]+\z/] $reaper_version,
  Stdlib::HTTPSUrl $reaper_source,
  Pattern[/\A[0-9a-f]{64}\z/] $reaper_checksum,
  Pattern[/\A[0-9]+\.[0-9]+\.[0-9]+(?:-beta[0-9]+)?\z/] $teamspeak_version,
  Stdlib::HTTPSUrl $teamspeak_source,
  Pattern[/\A[0-9a-f]{64}\z/] $teamspeak_checksum,
) {
  if $facts['os']['architecture'] != 'amd64' {
    fail('Desktop archive releases require amd64')
  }

  ensure_packages([
    'libasound2t64', 'libatomic1', 'libc6', 'libglib2.0-0t64',
    'libgbm1', 'libgtk-3-0t64', 'libnotify4', 'libnss3', 'libpulse0', 'libstdc++6',
    'libxss1', 'libxtst6', 'xz-utils',
  ])

  $reaper_dir = "/opt/reaper-${reaper_version}"
  $teamspeak_dir = "/opt/teamspeak-${teamspeak_version}"

  file { [$reaper_dir, $teamspeak_dir, '/usr/local/share/applications']:
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
  }

  archive { "/var/cache/reaper-${reaper_version}.tar.xz":
    ensure          => present,
    source          => $reaper_source,
    checksum_type   => 'sha256',
    checksum        => $reaper_checksum,
    extract         => true,
    extract_path    => $reaper_dir,
    extract_command => "tar -xJf %s -C ${reaper_dir} --strip-components=1 --no-same-owner --no-same-permissions",
    creates         => "${reaper_dir}/REAPER/reaper",
    cleanup         => true,
    require         => [File[$reaper_dir], Package['xz-utils']],
  }

  archive { "/var/cache/teamspeak-${teamspeak_version}.tar.xz":
    ensure          => present,
    source          => $teamspeak_source,
    checksum_type   => 'sha256',
    checksum        => $teamspeak_checksum,
    extract         => true,
    extract_path    => $teamspeak_dir,
    extract_command => "tar -xJf %s -C ${teamspeak_dir} --no-same-owner --no-same-permissions",
    creates         => "${teamspeak_dir}/TeamSpeak",
    cleanup         => true,
    require         => [File[$teamspeak_dir], Package['xz-utils']],
  }

  file { "${teamspeak_dir}/chrome-sandbox":
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '4755',
    require => Archive["/var/cache/teamspeak-${teamspeak_version}.tar.xz"],
  }

  file { '/usr/local/bin/reaper':
    ensure  => link,
    target  => "${reaper_dir}/REAPER/reaper",
    owner   => 'root',
    group   => 'root',
    require => Archive["/var/cache/reaper-${reaper_version}.tar.xz"],
  }

  file { '/usr/local/bin/teamspeak':
    ensure  => link,
    target  => "${teamspeak_dir}/TeamSpeak",
    owner   => 'root',
    group   => 'root',
    require => File["${teamspeak_dir}/chrome-sandbox"],
  }

  file { '/usr/local/share/applications/cockos-reaper.desktop':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => "[Desktop Entry]\nType=Application\nName=REAPER\nExec=/usr/local/bin/reaper %F\nIcon=${reaper_dir}/REAPER/Resources/main.png\nCategories=AudioVideo;Audio;\nTerminal=false\n",
    require => File['/usr/local/bin/reaper'],
  }

  file { '/usr/local/share/applications/teamspeak-client.desktop':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    content => "[Desktop Entry]\nType=Application\nName=TeamSpeak\nExec=/usr/local/bin/teamspeak %u\nIcon=${teamspeak_dir}/logo-256.png\nCategories=Network;Chat;\nTerminal=false\nMimeType=x-scheme-handler/ts3server;\n",
    require => File['/usr/local/bin/teamspeak'],
  }
}
