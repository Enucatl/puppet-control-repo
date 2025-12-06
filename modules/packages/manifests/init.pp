# modules/packages/manifests/init.pp
#
# Class to manage the installation of general system packages 
# defined in Hiera.

class packages (
  Array $list = []  
) {
  # 2. Define default attributes for the package resources.
  # We ensure the package is installed. We don't set a provider here 
  # as Puppet is usually smart enough to determine apt, yum, etc., 
  # based on the OS.
  if empty($list) {
    return()
  }

  $package_defaults = {
    ensure => installed,
  }
  
  # 3. Use create_resources to generate 'package' resources 
  # for every item found in the $package_list.
  # create_resources expects a hash where keys are resource titles 
  # (package names) and values are parameter hashes (defaults).
  # Since our Hiera list is an array, we first convert it to a hash 
  # where the key and value are the package name, then merge the defaults.
  
  # Map the array [pkg1, pkg2] into a hash {pkg1 => {}, pkg2 => {}}
  $package_hash = $list.reduce({}) |$memo, $pkg| {
    $memo + {$pkg => {}}
  }

  # Create the resources
  create_resources('package', $package_hash, $package_defaults)

}
