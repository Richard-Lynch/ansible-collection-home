# `homeassistant_update`

Updates Home Assistant Core (or the OS / Supervisor) with a pre-update backup, post-update
validation, and a rollback path.

```yaml
- hosts: homeassistant
  vars:
    homeassistant_base_url: "{{ homeassistant.base_url }}"
    homeassistant_token: "{{ ha_token }}"  # a long-lived access token from your secret store
  roles:
    - role: richard_lynch.home.homeassistant_update
```

| Variable | Default | Meaning |
|---|---|---|
| `homeassistant_update_component` | `core` | `core`, `os` or `supervisor` |
| `homeassistant_update_version` | `""` | empty = `version_latest` |
| `homeassistant_update_apply` | `false` | generate-don't-apply |
| `homeassistant_update_backup` | `true` | partial backup first — **this is what makes it reversible** |
| `homeassistant_update_min_free_mb` | `3000` | refuse to start below this |
| `homeassistant_update_settle_timeout` | `600` | seconds to wait for a healthy entity count |
| `homeassistant_update_min_entities` | `0` | `0` = derive from the pre-update count |
| `homeassistant_update_rollback` | `false` | auto-restore on validation failure |

## The failure this is built around

**HA answers on `:8123` while in recovery mode with only a small fraction of its entities.**
Every naive health check — port open, API 200, `core/info` returns a version — passes in that
state, and nothing works. So validation is judged on the **entity count against a pre-update
baseline**, not on the API answering.

The threshold is derived (90% of the pre-update count), because a hardcoded number goes stale as
the installation grows and a too-low one passes a broken start.

## Gates, and what each prevents

| Gate | Prevents |
|---|---|
| free disk ≥ `homeassistant_update_min_free_mb` | a half-pulled image **and** no room for the rollback backup — a partial backup is typically over a gigabyte |
| `core_check` **before** | updating onto a broken config, then blaming the update for the failed restart |
| backup read back from the Supervisor | discovering after a bad update that the backup step reported success and produced nothing |
| entity count vs baseline | recovery mode passing as healthy |
| `core_check` **after** | a config the new version rejects |

## Why the verdicts are not `.failed`

`failed_when: false` forces `.failed` to False **even on genuine failure**, so a rollback gated on
it could never fire — the one branch that has to work would be the one that cannot. The health and
config verdicts are computed from the entity numbers and the module's own message instead.

## Rollback

Automatic rollback is **off by default**: a restore is itself long and risky, and doing it
unattended on a failure nobody has looked at can turn a recoverable problem into a confusing one.
The failure message names the pre-update backup slug instead, so a restore is one deliberate
`homeassistant_supervisor` call (`action: backup_restore`, `backup_slug: <slug>`, `apply: true`).

Restores are **partial, Core only** — a full restore would also roll back add-ons and their data,
silently reverting unrelated work (an ESPHome device added yesterday, a Zigbee pairing).
