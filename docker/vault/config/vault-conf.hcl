api_addr = "https://hcv.home.arpa:8200"
ui = "false"

storage "file" {
  path    = "/vault/file"
}

listener "tcp" {
  address = "[::]:8200"
  tls_disable = "false"
  tls_cert_file = "/certificates/vault.crt"
  tls_key_file = "/certificates/vault.key"
}

