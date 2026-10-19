require 'json'
require 'net/http'
require 'openssl'

# Local bootstrap control uses exact-name TLS before sending any credentials.
request = JSON.parse(STDIN.read)
http = Net::HTTP.new('vault.home.arpa', 8200, nil)
http.ipaddr = '127.0.0.1'
http.use_ssl = true
http.ca_file = request.fetch('trust')
http.min_version = http.max_version = OpenSSL::SSL::TLS1_3_VERSION
http.open_timeout = 5
http.read_timeout = 30
headers = {'Content-Type' => 'application/json'}
headers['X-Vault-Token'] = File.read(request.fetch('token_file')).strip if request['auth']
message = Net::HTTPGenericRequest.new(request.fetch('method'), true, true,
  '/v1/' + request.fetch('path'), headers)
message.body = JSON.generate(request.fetch('body', {}))
response = http.request(message)
puts JSON.generate(status: response.code.to_i,
                   body: response.body.to_s.empty? ? {} : JSON.parse(response.body))
