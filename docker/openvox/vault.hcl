disable_mlock = true
api_addr = "https://vault.rehearsal.test:8200"
storage "file" { path = "/state/data" }
listener "tcp" {
  address = "0.0.0.0:8200"
  tls_cert_file = "/state/tls.crt"
  tls_key_file = "/state/tls.key"
  tls_min_version = "tls13"
  tls_max_version = "tls13"
}
