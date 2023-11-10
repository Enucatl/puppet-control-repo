# Class: tidy
#
# This class manages the tidy resource type for multiple files, applying specified
# settings to each file.
#
# Parameters:
#   - $files: A hash containing file-specific settings for the tidy resource type.
#   - $defaults: A hash containing default settings for the tidy resource type.
#
# Example Usage:
#   class { 'tidy':
#     files    => {
#       '/path/to/file1.txt' => {
#         'recurse' => true,
#         'matches' => '*.bak',
#         'age'     => '7d',
#         'type'    => 'r',
#         'backup'  => false,
#       },
#       '/path/to/file2.txt' => {
#         'recurse' => false,
#         'matches' => '*.tmp',
#         'age'     => '14d',
#         'type'    => 'r',
#         'backup'  => true,
#       },
#     },
#     defaults => {
#       'recurse' => true,
#       'age'     => '30d',
#       'type'    => 'r',
#       'backup'  => true,
#     }
#   }
#
class tidy (
  Hash $files,
  Hash $defaults,
) {
  # Create resources using the tidy type to manage files.
  create_resources(tidy, $files, $defaults)
}
