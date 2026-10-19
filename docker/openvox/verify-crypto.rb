#!/opt/puppetlabs/puppet/bin/ruby

require 'base64'
require 'openssl'
require 'optparse'

checks = []
parser = OptionParser.new do |options|
  options.banner = 'Usage: verify-crypto.rb [--key PATH] [--cert PATH] [--crl PATH] [KEY ...]'
  options.on('--key PATH') { |path| checks << [:key, path] }
  options.on('--cert PATH') { |path| checks << [:cert, path] }
  options.on('--crl PATH') { |path| checks << [:crl, path] }
end
parser.parse!
checks.concat(ARGV.map { |path| [:key, path] })
abort parser.to_s if checks.empty?

checks.each do |kind, path|
  requirement = "ML-DSA-44#{kind == :key ? ', seed-only PKCS#8' : ''}"
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
            !crls.empty? && crls.all? do |encoded|
              OpenSSL::X509::CRL.new(encoded).signature_algorithm == 'ML-DSA-44'
            end
          end
  raise 'Unexpected algorithm or encoding' unless valid

  puts "PASS #{kind}: #{path} (#{requirement})"
rescue StandardError
  abort "FAIL #{kind}: #{path} (expected #{requirement})"
end
