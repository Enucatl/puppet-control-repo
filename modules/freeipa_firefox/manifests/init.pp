# Add FreeIPA-enrolled CAs alongside the browsers' built-in public trust stores.
class freeipa_firefox {
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
      '/etc/ca-certificates/update.d/browser-trust',
      '/etc/firefox/policies/certs',
      '/etc/chromium-browser/policies/managed',
    ],
  }
}
