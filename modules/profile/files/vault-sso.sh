# /etc/profile sources this only for login shells; avoid automated SSH commands.
export VAULT_ADDR="${VAULT_ADDR:-https://vault.home.arpa:8200}"
case $- in
  *i*) /usr/local/bin/vault-sso-login >/dev/null 2>&1 || true ;;
esac
