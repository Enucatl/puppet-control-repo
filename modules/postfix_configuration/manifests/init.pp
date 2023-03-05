class postfix_configuration {

  include postfix

  postfix::hash { '/etc/postfix/sasl_passwd':
    content => "${lookup('postfix::relayhost')} ${lookup('smtp_sasl_username')}:${lookup('smtp_sasl_password')}",
    require => Class['postfix'],
  }

  postfix::conffile { 'recipient_canonical':
    content => "/.+/    ${lookup('recipient_canonical')}",
  }

  postfix::conffile { 'sender_canonical':
    content => "/^(.*)@(.*).(home.arpa)/    ${lookup('smtp_sasl_username')}+\${1}.\${2}.\${3}@gmail.com",
  }

}
