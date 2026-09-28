api_addr = "http://vault:8200"
ui = "false"

storage "file" {
  path    = "/vault/file"
}

listener "tcp" {
  address = "[::]:8200"
  tls_disable = "true"
}

disable_mlock = true
