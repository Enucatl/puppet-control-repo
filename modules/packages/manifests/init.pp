class packages (
  Array $list    = [],
  Array $ppas    = [],
  Hash  $sources = {},
) {
  include apt

  # 1. Create PPA resources
  $ppas.each |String $ppa_name| {
    apt::ppa { $ppa_name: }
  }

  # 2. Create Source resources (for Docker, etc.)
  $sources.each |String $source_name, Hash $config| {
    apt::source { $source_name:
      * => $config, # This passes all Hiera keys as parameters
    }
  }

  # 3. The "Magic" Ordering
  Apt::Ppa <| |>       -> Class['apt::update']
  Apt::Source <| |>    -> Class['apt::update'] # Added this
  Class['apt::update'] -> Package <| provider == 'apt' |>

  # 4. Create the package resources
  $list.each |String $package_name| {
    if !defined(Package[$package_name]) {
      package { $package_name:
        ensure   => installed,
        provider => 'apt',
      }
    }
  }
}
