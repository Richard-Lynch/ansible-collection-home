# `homeassistant_secrets`

Writes the credentials Home Assistant's `configuration.yaml` references with
`!secret` into a **marked block** of `secrets.yaml`, reading each value from
1Password with `richard_lynch.common.onepassword_cached`. Runs over SSH into the
_Advanced SSH & Web Terminal_ add-on.

```yaml
- hosts: homeassistant
  roles:
    - role: richard_lynch.home.homeassistant_secrets
      vars:
        homeassistant_secrets_vault: "My-Vault"
        homeassistant_secrets:
          - {
              key: some_service_password,
              item: "service/ha/some-service",
              field: password,
            }
        homeassistant_secrets_literal:
          some_service_username: "user@example.com"
```

| Variable                        | Default                | Meaning                                                     |
| ------------------------------- | ---------------------- | ----------------------------------------------------------- |
| `homeassistant_secrets_path`    | `/config/secrets.yaml` | HA's secrets file inside the add-on                         |
| `homeassistant_secrets`         | `[]`                   | `{key, item, field, vault?}` entries read from 1Password    |
| `homeassistant_secrets_vault`   | `""`                   | vault for every entry that names none                       |
| `homeassistant_secrets_literal` | `{}`                   | non-secret `{key: value}` pairs written into the same block |
| `homeassistant_secrets_apply`   | `false`                | **generate-don't-apply**; default writes nothing            |

## Safety properties

- **It refuses to run with nothing to manage**, so an empty block can never read
  as "managed and correct".
- **Only the marked block is owned.** Keys added by hand or by an integration's
  setup flow outside the markers are left untouched; templating the whole file
  would delete them silently.
- **It refuses to write to the wrong place.** `.HA_VERSION` must exist beside
  the secrets file.
- **No value reaches the log.** The read and the block write are `no_log`; the
  report names keys only.
- **The dry run diffs.** The block task runs under `check_mode` when not
  applying, rather than being skipped.
- **HA validates its config afterwards** (`core_check` via the Supervisor API),
  proving the `!secret` references resolve; the previous file is kept as
  `secrets.yaml.<timestamp>.bak`.

Each distinct uncached 1Password read is a real `op` call; under desktop auth
that is an approval prompt, so use a service account for unattended runs.
