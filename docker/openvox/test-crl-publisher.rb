require 'openssl'
require 'open3'
require 'rbconfig'
require 'json'

# Fixtures remain in memory; the actual CA files and revocation state are read-only.
ca = '/etc/puppetlabs/puppetserver/ca'
current = OpenSSL::X509::CRL.new(File.read("#{ca}/ca_crl.pem"))
issuer_key = OpenSSL::PKey.read(File.read("#{ca}/ca_key.pem"))
fixtures = { 'current_signed_crl' => [current.to_pem, true],
             'current_issuer_bundle' => [File.read("#{ca}/ca_crl.pem"), true] }

tampered = current.to_der
last = tampered.bytesize - 1
tampered.setbyte(last, tampered.getbyte(last) ^ 1)
fixtures['tampered_signature'] = [OpenSSL::X509::CRL.new(tampered).to_pem, false]

wrong_signer = OpenSSL::X509::CRL.new(current.to_der)
wrong_signer.sign(OpenSSL::PKey.generate_key('ML-DSA-44'), nil)
fixtures['wrong_signer_same_issuer'] = [wrong_signer.to_pem, false]

wrong_issuer = OpenSSL::X509::CRL.new(current.to_der)
wrong_issuer.issuer = OpenSSL::X509::Name.parse('/CN=Unrelated_publisher_fixture')
wrong_issuer.sign(issuer_key, nil)
fixtures['wrong_issuer_valid_signature'] = [wrong_issuer.to_pem, false]

expired = OpenSSL::X509::CRL.new(current.to_der)
expired.last_update = Time.now - 120
expired.next_update = Time.now - 60
expired.sign(issuer_key, nil)
fixtures['expired_valid_signature'] = [expired.to_pem, false]

future = OpenSSL::X509::CRL.new(current.to_der)
future.last_update = Time.now + 60
future.next_update = Time.now + 120
future.sign(issuer_key, nil)
fixtures['future_last_update'] = [future.to_pem, false]

missing_next_update = OpenSSL::X509::CRL.new
missing_next_update.version = 1
missing_next_update.issuer = current.issuer
missing_next_update.last_update = Time.now - 60
missing_next_update.sign(issuer_key, nil)
fixtures['missing_next_update'] = [missing_next_update.to_pem, false]

classical = OpenSSL::X509::CRL.new(current.to_der)
classical.sign(OpenSSL::PKey::RSA.new(2048), OpenSSL::Digest.new('SHA256'))
fixtures['classical_signature'] = [classical.to_pem, false]
fixtures['empty_input'] = ['', false]

results = fixtures.to_h do |name, (pem, expected)|
  _output, _error, status = Open3.capture3(
    RbConfig.ruby, '/rehearsal/verify-crypto.rb', '--crl', '/dev/stdin',
    '--issuer', "#{ca}/ca_crt.pem", stdin_data: pem
  )
  [name, { 'accepted' => status.success?, 'expected' => expected, 'passed' => status.success? == expected }]
end
puts JSON.generate('checks' => results, 'all_passed' => results.values.all? { |check| check['passed'] },
                   'actual_ca_changed' => false, 'fixture_material_written' => false)
exit(results.values.all? { |check| check['passed'] } ? 0 : 1)
