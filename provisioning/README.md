# configure the router
```bash
uv sync
uv run ansible-playbook -i inventory/router.yml router-config.yml --vault-password-file vars/password
```
