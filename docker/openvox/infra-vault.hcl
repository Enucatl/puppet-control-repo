api_addr = "https://hcv.home.arpa:8200"
ui = false
disable_mlock = true
raw_storage_endpoint = false

storage "file" {
  path = "/vault/file"
}

listener "tcp" {
  address = "[::]:8200"
  tls_disable = false
  tls_cert_file = "/certificates/vault_chain.crt"
  tls_key_file = "/certificates/vault.key"
  tls_client_ca_file = "/certificates/ca.crt"
  tls_min_version = "tls13"
  tls_max_version = "tls13"
  # Enrollment needs a token before it has a certificate; cert auth validates it.
  tls_require_and_verify_client_cert = false
  tls_disable_client_certs = false
}
