# Proton DNS for enucatl.com

This configuration manages ownership verification and Proton's seven mail records.
Proton's DNS values are public
and belong in Git; credentials do not. Keep `.terraform.lock.hcl` in Git.

## Credentials and local state

Use the existing Vault login and the `cloudflare_api_token` and
`cloudflare_zone_id` fields at `kv/puppet`. The token needs DNS Edit permission
for enucatl.com. The existing `cloudflare_account_id` is not needed for DNS
records. Keep credentials in Vault; do not copy the token into Terraform files.

The commands below run in a subshell and stop if Vault retrieval fails or returns
an empty token. Do not enable shell tracing or Terraform debug logging.
[The provider reads `CLOUDFLARE_API_TOKEN`](https://github.com/cloudflare/terraform-provider-cloudflare/blob/main/docs/index.md).
Do not use Terraform Vault data sources for credentials: retrieved secrets can
be stored in [Terraform state](https://registry.terraform.io/providers/hashicorp/vault/latest/docs).

State is local to this directory on this host. Back up `terraform.tfstate` and
`terraform.tfstate.backup` securely off-host after each apply, and test restoration.
Git is not a state backup. Run from this directory and serialize operations;
do not create independent state in another checkout. Saved plans and state are
ignored; save plans using a `.tfplan` or `.plan` suffix.

## Plan and apply

Before every apply, inspect **all pages** of existing records in the Cloudflare
DNS dashboard. If the matching ownership TXT already exists, import its record
ID using the command below before planning. Import matching mail records too,
resolve conflicting email records, and preserve unrelated TXT records.
Create receiving addresses in Proton before changing MX; `hello@enucatl.com`
was created before the mail records were configured.

```bash
(
  set +x
  set -euo pipefail
  TF_VAR_cloudflare_zone_id=$(vault kv get -field=cloudflare_zone_id kv/puppet)
  test -n "$TF_VAR_cloudflare_zone_id"
  export TF_VAR_cloudflare_zone_id
  CLOUDFLARE_API_TOKEN=$(vault kv get -field=cloudflare_api_token kv/puppet)
  test -n "$CLOUDFLARE_API_TOKEN"
  export CLOUDFLARE_API_TOKEN
  terraform init -lockfile=readonly
  terraform fmt -check
  terraform validate
  # Only if a matching record exists; replace RECORD_ID before uncommenting:
  # terraform import cloudflare_dns_record.proton_verification "$TF_VAR_cloudflare_zone_id/RECORD_ID"
  terraform plan -out=mail.tfplan
  terraform show mail.tfplan
  # Continue only if the plan affects solely the intended Proton DNS records:
  read -rp 'Type apply after reviewing the plan: ' confirmation
  test "$confirmation" = apply
  terraform apply mail.tfplan
  dig @1.1.1.1 +short TXT enucatl.com
  dig @8.8.8.8 +short TXT enucatl.com
  terraform plan -detailed-exitcode
)
```

Check that public DNS returns the exact ownership value, then ask Proton to verify
the domain. The final plan must exit 0 (no changes); exit 2 means changes remain.

## Stage two: mail routing

The exact two MX records (including priorities), SPF TXT, three DKIM CNAME
names and targets, and DMARC TXT provided by Proton are explicit resources in
`main.tf`. They use full DNS names, TTL `3600`, and `proxied = false` for each
DKIM CNAME. The ownership resource remains managed.

Inspect existing DNS again. Import matching records using
`terraform import cloudflare_dns_record.RESOURCE_NAME 'ZONE_ID/RECORD_ID'`.
Resolve conflicting email records: update/import an existing SPF record rather
than creating a second SPF TXT, account for other authorized senders, and remove
obsolete MX records at cutover. Cloudflare records outside this state are not
automatically removed by Terraform.

Repeat the credential, format, validation, saved-plan review, and apply workflow
above with `mail.tfplan`. This plan should affect only Proton email records.
Check public MX/TXT records for `enucatl.com`, each DKIM CNAME, and TXT at
`_dmarc.enucatl.com` using both resolvers. Verify each record in Proton and confirm
a final plan has no changes. Send mail both to and from an external mailbox;
inspect the received message headers for SPF, DKIM, and DMARC passing.
Follow [Proton's Cloudflare setup guide](https://proton.me/support/custom-domain-cloudflare).

After applying, finish verification in Proton and the external send/receive check.
