# configure the router

## Setup

```bash
uv sync
```

## Testing progression

### 1 — syntax check (local only, no vault/router needed)
```bash
uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --syntax-check
```

### 2 — diff (read-only, safe)
Fetches `show configuration commands` from the router, renders the template locally, and diffs both sides in set-command format.
```bash
uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags diff
```

### 3 — dry-run apply (no commit)
Enters configure mode, loads the commands into candidate config, reports what would change, then discards.
```bash
uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags apply --check
```

### 4 — apply
```bash
uv run ansible-playbook -i inventory/router.yml router.yml --vault-password-file vars/password --tags apply
```

## Workflow for any change

1. Edit a partial under `templates/partials/`
2. `--tags diff` to review the delta
3. `--tags apply` to push it
4. `--tags diff` again to confirm clean

## Manual deletes

Add `delete ...` lines to `templates/partials/deletes.j2` for stale nodes that the set-command partials won't naturally overwrite (renamed peers, retired rules, removed features). Remove the line once confirmed absent on the router.
