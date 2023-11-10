# Class: postfix_configuration
#
# This class manages the configuration of the Postfix mail server by including
# the postfix class and defining hash and conffile resources for specific
# configurations.
#
# This configuration is used to modify the recipient (destination) addresses
# for incoming emails. Canonicalization, in this context, refers to the process
# of transforming or normalizing email addresses to a consistent format.
#
# recipient: send all emails to a fixed address, specified in hiera
# sender: user a fake username built from the SMTP username + hostname, using
# the gmail + phantom address capability
#
# Parameters: None
#
# Example Usage: class { 'postfix_configuration': }
#
class postfix_configuration {

  # include - Include the postfix class to ensure Postfix is installed and configured.
  #
  # Parameters:
  #   None
  #
  include postfix

  # postfix::hash - Manage the Postfix hash file for SASL authentication.
  #
  # Parameters:
  #   - path: The path to the Postfix hash file.
  #   - content: The content of the hash file containing SASL authentication information.
  #   - require: Specifies dependencies that must be met before applying the hash file.
  #
  postfix::hash { '/etc/postfix/sasl_passwd':
    content => "${lookup('postfix::relayhost')} ${lookup('smtp_sasl_username')}:${lookup('smtp_sasl_password')}",
    require => Class['postfix'],
  }

  # postfix::conffile - Manage Postfix conffiles for recipient canonicalization.
  #
  # Parameters:
  #   - name: The name of the conffile resource.
  #   - content: The content of the conffile specifying recipient canonicalization rules.
  #
  postfix::conffile { 'recipient_canonical':
    content => "/.+/    ${lookup('recipient_canonical')}",
  }

  # postfix::conffile - Manage Postfix conffiles for sender canonicalization.
  #
  # Parameters:
  #   - name: The name of the conffile resource.
  #   - content: The content of the conffile specifying sender canonicalization rules.
  #
  postfix::conffile { 'sender_canonical':
    content => "/^(.*)@(.*).(home.arpa)/    ${lookup('smtp_sasl_username')}+\${1}.\${2}.\${3}@gmail.com",
  }
}
