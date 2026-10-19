require 'net/http'
require 'openssl'

http = Net::HTTP.new('docker.home.arpa', 8140, nil)
http.ipaddr = '127.0.0.1'
http.use_ssl = true
http.ca_file = '/trust/root.pem'
http.min_version = OpenSSL::SSL::TLS1_3_VERSION
http.open_timeout = 2
http.read_timeout = 2
abort 'CA endpoint unavailable' unless http.get('/puppet-ca/v1/certificate/ca').code == '200'
response = http.get('/status/v1/simple')
abort 'OpenVox services not ready' unless response.code == '200' && response.body.strip == 'running'
