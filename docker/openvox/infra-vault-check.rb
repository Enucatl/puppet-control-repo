require 'json'
require 'net/http'
require 'openssl'

http = Net::HTTP.new('vault.home.arpa', 8200, nil)
http.use_ssl = true
http.ca_file = '/etc/ssl/certs/ca-certificates.crt'
http.min_version = http.max_version = OpenSSL::SSL::TLS1_3_VERSION
ssl = '/etc/puppetlabs/puppet/ssl'
http.cert = OpenSSL::X509::Certificate.new(File.read("#{ssl}/certs/openvox.home.arpa.pem"))
http.key = OpenSSL::PKey.read(File.read("#{ssl}/private_keys/openvox.home.arpa.pem"))
http.extra_chain_cert = File.read('/trust/openvox-chain.pem').scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map { |pem| OpenSSL::X509::Certificate.new(pem) }
http.open_timeout = 5
http.read_timeout = 10
http.start do |connection|
  login = Net::HTTP::Post.new('/v1/auth/cert/login', 'Content-Type' => 'application/json')
  login.body = JSON.generate(name: 'puppet-server')
  response = connection.request(login)
  abort "Compiler cert-auth returned HTTP #{response.code}" unless response.code == '200'
  auth = JSON.parse(response.body).fetch('auth')
  abort 'Unexpected compiler privileges' unless auth.fetch('policies') == ['puppet-server']
  headers = {'X-Vault-Token' => auth.fetch('client_token')}
  response = connection.request(Net::HTTP::Get.new('/v1/kv/data/puppet', headers))
  abort "Compiler KV lookup returned HTTP #{response.code}" unless response.code == '200'
  abort 'Missing bootstrap Hiera marker' unless JSON.parse(response.body).dig('data', 'data', 'atlas_infra::marker') == 'atlas-infra-v1'
  response = connection.request(Net::HTTP::Get.new('/v1/kv/data/not-permitted', headers))
  abort 'Compiler has unrelated KV access' unless response.code == '403'
end
puts JSON.generate(compiler_cert_auth: true, hiera_kv_marker: true, unrelated_kv_denied: true)
