#!/opt/puppetlabs/puppet/bin/ruby

require 'base64'
require 'openssl'
require 'optparse'

checks = []
issuer_file = nil
parser = OptionParser.new do |options|
  options.banner = 'Usage: verify-crypto.rb [--key PATH] [--cert PATH] [--crl PATH] [--issuer CA_BUNDLE] [KEY ...]'
  options.on('--key PATH') { |path| checks << [:key, path] }
  options.on('--cert PATH') { |path| checks << [:cert, path] }
  options.on('--crl PATH') { |path| checks << [:crl, path] }
  options.on('--issuer PATH') { |path| issuer_file = path }
end
parser.parse!
checks.concat(ARGV.map { |path| [:key, path] })
abort parser.to_s if checks.empty?

checks.each do |kind, path|
  requirement = "ML-DSA-44#{kind == :key ? ', seed-only PKCS#8' : ''}#{kind == :crl && issuer_file ? ', verified issuer/signature/freshness' : ''}"
  pem = File.read(path)
  valid = case kind
          when :key
            match = /\A\s*-----BEGIN PRIVATE KEY-----\r?\n([A-Za-z0-9+\/=\r\n]+)-----END PRIVATE KEY-----\s*\z/.match(pem)
            raise 'Expected unencrypted PKCS#8' unless match

            key = OpenSSL::ASN1.decode(Base64.strict_decode64(match[1].delete("\r\n")))
            payload = key.value[2].value
            OpenSSL::PKey.read(pem)
            key.value[1].value[0].oid == '2.16.840.1.101.3.4.3.17' &&
              payload.bytesize == 34 && payload.getbyte(0) == 0x80 && payload.getbyte(1) == 32
          when :cert
            certs = pem.scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m)
            !certs.empty? && certs.all? do |encoded|
              cert = OpenSSL::X509::Certificate.new(encoded)
              cert.signature_algorithm == 'ML-DSA-44' &&
                OpenSSL::ASN1.decode(cert.public_key.public_to_der).value[0].value[0].oid == '2.16.840.1.101.3.4.3.17'
            end
          when :crl
            crls = pem.scan(/-----BEGIN X509 CRL-----.*?-----END X509 CRL-----/m)
            issuers = if issuer_file
                        File.read(issuer_file).scan(/-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----/m).map do |encoded|
                          OpenSSL::X509::Certificate.new(encoded)
                        end
                      end
            now = Time.now
            !crls.empty? && crls.all? do |encoded|
              crl = OpenSSL::X509::CRL.new(encoded)
              issuer = issuers&.find { |cert| cert.subject.to_der == crl.issuer.to_der }
              crl.signature_algorithm == 'ML-DSA-44' && (!issuer_file || (
                issuer && OpenSSL::ASN1.decode(issuer.public_key.public_to_der).value[0].value[0].oid == '2.16.840.1.101.3.4.3.17' &&
                crl.verify(issuer.public_key) && crl.last_update && crl.next_update &&
                crl.last_update <= now && now < crl.next_update
              ))
            end
          end
  raise 'Unexpected algorithm or encoding' unless valid

  puts "PASS #{kind}: #{path} (#{requirement})"
rescue StandardError
  abort "FAIL #{kind}: #{path} (expected #{requirement})"
end
