# Add FreeIPA-enrolled CAs alongside the browsers' built-in public trust stores.
class freeipa_firefox (
  String $username = 'user',
) {
  ensure_packages(['ca-certificates', 'python3'])

  file { [
    '/etc/firefox',
    '/etc/firefox/policies',
    '/etc/firefox/policies/certs',
    '/etc/chromium-browser',
    '/etc/chromium-browser/policies',
    '/etc/chromium-browser/policies/managed',
  ]:
    ensure => directory,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
  }

  file { '/etc/firefox/policies/baseline.json':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/freeipa_firefox/firefox-policies.json',
    require => File['/etc/firefox/policies'],
  }

  file { '/etc/firefox/policies/user.js':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0644',
    source  => 'puppet:///modules/freeipa_firefox/firefox-user.js',
    require => File['/etc/firefox/policies'],
  }

  file { '/usr/local/bin/firefox-profile-prefs':
    ensure => file,
    owner  => 'root',
    group  => 'root',
    mode   => '0755',
    source => 'puppet:///modules/freeipa_firefox/firefox-profile-prefs.py',
  }

  $home = $username ? { 'root' => '/root', default => "/home/${username}" }
  exec { 'sync-firefox-profile-preferences':
    command     => '/usr/local/bin/firefox-profile-prefs',
    unless      => '/usr/local/bin/firefox-profile-prefs --check',
    onlyif      => "/usr/bin/test -d ${home}",
    user        => $username,
    environment => ["HOME=${home}"],
    require     => [
      Package['python3'],
      File['/usr/local/bin/firefox-profile-prefs'],
      File['/etc/firefox/policies/user.js'],
    ],
  }

  file { '/etc/ca-certificates/update.d/browser-trust':
    ensure  => file,
    owner   => 'root',
    group   => 'root',
    mode    => '0755',
    source  => 'puppet:///modules/freeipa_firefox/browser-trust.py',
    require => Package[['ca-certificates', 'python3']],
  }

  exec { 'generate-browser-policies':
    command => '/etc/ca-certificates/update.d/browser-trust',
    unless  => '/etc/ca-certificates/update.d/browser-trust --check',
    require => File[
      '/etc/firefox/policies/baseline.json',
      '/etc/ca-certificates/update.d/browser-trust',
      '/etc/firefox/policies/certs',
      '/etc/chromium-browser/policies/managed',
    ],
  }
}
