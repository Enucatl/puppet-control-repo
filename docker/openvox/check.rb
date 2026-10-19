require 'openssl'
require 'net/http'
require 'json'

host, port, path, identity = ARGV
connection = Net::HTTP.new(host, Integer(port), nil)
connection.use_ssl = true
connection.ca_file = '/trust/root.pem'
connection.open_timeout = 5
connection.read_timeout = 30
if identity == 'agent'
  cert = '/agent/ssl/certs/rehearsal.home.arpa.pem'
  key = '/agent/ssl/private_keys/rehearsal.home.arpa.pem'
elsif identity == 'server'
  cert = '/etc/puppetlabs/puppet/ssl/certs/openvox.rehearsal.test.pem'
  key = '/etc/puppetlabs/puppet/ssl/private_keys/openvox.rehearsal.test.pem'
end
if cert
  connection.cert = OpenSSL::X509::Certificate.new(File.read(cert))
  connection.key = OpenSSL::PKey.read(File.read(key))
  connection.extra_chain_cert = File.read('/trust/openvox-chain.pem').scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map { |pem| OpenSSL::X509::Certificate.new(pem) }
end
method = ENV.fetch('REQUEST_METHOD', 'GET')
request = Net::HTTP.const_get(method.capitalize).new(path)
request['Content-Type'] = 'application/json'
request['Accept'] = 'application/json'
request.body = STDIN.read unless STDIN.tty?
response = connection.start { |http| http.request(request) }
puts JSON.generate(status: response.code.to_i, body: response.body)
