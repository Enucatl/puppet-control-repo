# Proxmox workflows

This local component module installs the Chronicle backup and weekly vLLM workflows,
their shared Python library, and their systemd units. Site configuration continues
through `profile::chronicle_backup`, `profile::vllm_weekly_window`, and
`profile::proxmox_orchestration`; existing profile Hiera keys remain valid.

| Class | Installed script/library |
|---|---|
| `proxmox_workflows::chronicle_backup` | `/usr/local/sbin/chronicle-backup-orchestrator` |
| `proxmox_workflows::vllm_weekly_window` | `/usr/local/sbin/vllm-weekly-window` |
| `proxmox_workflows::library` | `/usr/local/lib/proxmox_orchestration.py` |

The workflows remain separate. They share Vault authentication, wake/unlock, SSH,
port-waiting, and locking helpers. Their existing CLI arguments, lock paths, cleanup
rules, schedules, and timeout behavior are unchanged. The shared-library path is
passed through the existing profile wrapper before either workflow is declared.

The host supplies Python 3 and the existing operational tools: Vault, SSH, Expect,
wakeonlan, netcat, jq, and Proxmox commands. The existing Puppet certificate identity
reads `kv/wolf` field `proxmox-cortex`; no secret path changes accompany this extraction.
The backup profile still disables the native PVE job schedule before enabling its
systemd timer, and both workflows retain their persistent timers.

Tests remain in the repository's `tests/` directory and run with `uv run --frozen pytest`.
The workflow tests use fake commands; they do not start VMs, run backups, or power off
hosts. Real operations should continue through the installed systemd services.
