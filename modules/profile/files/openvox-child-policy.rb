# Initialize Puppet/Vault TLS before giving subprocesses the system TLS policy.
ENV['OPENSSL_CONF'] = '/etc/puppetlabs/puppet/openssl.cnf'
require 'openssl'
OpenSSL::SSL::SSLContext.new
ENV['OPENSSL_CONF'] = '/etc/ssl/openssl.cnf'
