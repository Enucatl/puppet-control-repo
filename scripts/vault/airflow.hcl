path "airflow/data/connections/*" {
  capabilities = ["read", "list"]
}

path "airflow/data/variables/*" {
  capabilities = ["read", "list"]
}
