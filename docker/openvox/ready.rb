require 'net/http'
require 'openssl'

http = Net::HTTP.new('openvox.rehearsal.test', 8140, nil)
http.use_ssl = true
http.ca_file = '/trust/root.pem'
http.open_timeout = 2
http.read_timeout = 2
abort 'CA endpoint unavailable' unless http.get('/puppet-ca/v1/certificate/ca').code == '200'
