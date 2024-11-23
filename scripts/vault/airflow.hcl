path "secret/data/enucatl_bot" {
    capabilities = ["read"]
}

path "secret/data/airflow" {
    capabilities = ["read"]
}

# token_renewal_policy.hcl
path "auth/token/lookup-self" {
  capabilities = ["read"]
}

path "auth/token/renew-self" {
  capabilities = ["update"]
}
