terraform {
  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 5.26"
    }
  }

  backend "local" {}
}

provider "cloudflare" {}

variable "cloudflare_zone_id" {
  description = "Cloudflare zone ID for enucatl.com (nonsecret)."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{32}$", var.cloudflare_zone_id))
    error_message = "Provide the 32-character hexadecimal Cloudflare zone ID for enucatl.com."
  }
}

resource "cloudflare_dns_record" "proton_verification" {
  zone_id = var.cloudflare_zone_id
  name    = "enucatl.com"
  type    = "TXT"
  ttl     = 3600
  content = "protonmail-verification=7adb6a88bba55d62363b23ef79a50fa01f644816"
}

resource "cloudflare_dns_record" "proton_mx" {
  zone_id  = var.cloudflare_zone_id
  name     = "enucatl.com"
  type     = "MX"
  ttl      = 3600
  content  = "mail.protonmail.ch"
  priority = 10
}

resource "cloudflare_dns_record" "proton_mx_secondary" {
  zone_id  = var.cloudflare_zone_id
  name     = "enucatl.com"
  type     = "MX"
  ttl      = 3600
  content  = "mailsec.protonmail.ch"
  priority = 20
}

resource "cloudflare_dns_record" "proton_spf" {
  zone_id = var.cloudflare_zone_id
  name    = "enucatl.com"
  type    = "TXT"
  ttl     = 3600
  content = "v=spf1 include:_spf.protonmail.ch ~all"
}

resource "cloudflare_dns_record" "proton_dkim" {
  zone_id = var.cloudflare_zone_id
  name    = "protonmail._domainkey.enucatl.com"
  type    = "CNAME"
  ttl     = 3600
  content = "protonmail.domainkey.dnis53nyssekk4pjcxnoj3jkber2qjfkqeehp7h4jjxtnn4lzlr3q.domains.proton.ch"
  proxied = false
}

resource "cloudflare_dns_record" "proton_dkim_2" {
  zone_id = var.cloudflare_zone_id
  name    = "protonmail2._domainkey.enucatl.com"
  type    = "CNAME"
  ttl     = 3600
  content = "protonmail2.domainkey.dnis53nyssekk4pjcxnoj3jkber2qjfkqeehp7h4jjxtnn4lzlr3q.domains.proton.ch"
  proxied = false
}

resource "cloudflare_dns_record" "proton_dkim_3" {
  zone_id = var.cloudflare_zone_id
  name    = "protonmail3._domainkey.enucatl.com"
  type    = "CNAME"
  ttl     = 3600
  content = "protonmail3.domainkey.dnis53nyssekk4pjcxnoj3jkber2qjfkqeehp7h4jjxtnn4lzlr3q.domains.proton.ch"
  proxied = false
}

resource "cloudflare_dns_record" "proton_dmarc" {
  zone_id = var.cloudflare_zone_id
  name    = "_dmarc.enucatl.com"
  type    = "TXT"
  ttl     = 3600
  content = "v=DMARC1; p=quarantine"
}
