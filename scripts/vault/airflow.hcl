path "airflow/data/airflow/*" {
    capabilities = ["read"]
}

# Allow the token to generate a new SecretID for the 'airflow' AppRole.
# This enables the self-service "pull" model for the Vault Agent.
path "auth/approle/role/airflow/secret-id" {
  capabilities = ["update"] # 'update' is the capability for this write-like operation
}
