# Bootstrap from an official binary; desktop Hiera schedules weekly self-updates.
class profile::typst {
  $target = $facts['os']['architecture'] ? {
    'amd64'   => 'x86_64-unknown-linux-musl',
    'x86_64'  => 'x86_64-unknown-linux-musl',
    'arm64'   => 'aarch64-unknown-linux-musl',
    'aarch64' => 'aarch64-unknown-linux-musl',
    default   => fail("Unsupported architecture for Typst: ${facts['os']['architecture']}"),
  }
  $checksum = $target ? {
    'x86_64-unknown-linux-musl'  => 'a6d077d0a95eed5a2eba715b2dae06be954f624ccbf85758a03f389ded33118c',
    'aarch64-unknown-linux-musl' => '5aa8d74a3d906e60ea12a66ac2f37f8eef1b14cbad7182a745e393a10c23dcee',
  }

  ['ca-certificates', 'curl', 'xz-utils'].each |String $package_name| {
    if !defined(Package[$package_name]) {
      package { $package_name:
        ensure => installed,
      }
    }
  }

  archive { '/var/cache/typst.tar.xz':
    ensure          => present,
    source          => "https://github.com/typst/typst/releases/download/v0.15.1/typst-${target}.tar.xz",
    checksum_type   => 'sha256',
    checksum        => $checksum,
    extract         => true,
    extract_path    => '/usr/local/bin',
    extract_command => "tar -xJf %s -C /usr/local/bin --strip-components=1 typst-${target}/typst",
    creates         => '/usr/local/bin/typst',
    cleanup         => true,
    require         => Package[['ca-certificates', 'curl', 'xz-utils']],
  }

  file { '/usr/local/bin/typst':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0755',
    require => Archive['/var/cache/typst.tar.xz'],
  }
}
