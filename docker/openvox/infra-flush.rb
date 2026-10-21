require 'net/http'
require 'openssl'

http = Net::HTTP.new('openvox.home.arpa', 8140, nil)
http.ipaddr = '127.0.0.1'
http.use_ssl = true
http.ca_file = '/etc/ssl/certs/ca-certificates.crt'
ssl = '/etc/puppetlabs/puppet/ssl'
http.cert = OpenSSL::X509::Certificate.new(File.read("#{ssl}/certs/openvox.home.arpa.pem"))
http.key = OpenSSL::PKey.read(File.read("#{ssl}/private_keys/openvox.home.arpa.pem"))
http.extra_chain_cert = File.read('/trust/openvox-chain.pem').scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map do |pem|
  OpenSSL::X509::Certificate.new(pem)
end
http.min_version = OpenSSL::SSL::TLS1_3_VERSION
http.open_timeout = 5
http.read_timeout = 10
%w[production dev].each do |environment|
  response = http.request(Net::HTTP::Delete.new("/puppet-admin-api/v1/environment-cache?environment=#{environment}"))
  abort "Environment cache invalidation returned HTTP #{response.code}" unless response.code == '204'
end
