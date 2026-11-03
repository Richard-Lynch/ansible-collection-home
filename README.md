<!--
Copyright: (c) 2026, Richard Lynch
SPDX-License-Identifier: GPL-3.0-or-later
-->

# richard_lynch.home

Home automation roles and plugins: Home Assistant configuration, dashboards, secrets, updates and backup schedule through its own APIs; declared Zigbee (ZHA) bindings reconciled against the live devices; and a Canon PIXMA printer's settings, password and maintenance jobs through its Remote UI.

## Roles

| Role | Description |
| ---- | ----------- |
| `canon_printer` | Manage a Canon PIXMA printer through its Remote UI admin API |
| `config_zigbee_bindings` | Converge Zigbee direct bindings (switch -> light) via ZHA |
| `homeassistant_backup` | Set Home Assistant's native backup schedule via the websocket API |
| `homeassistant_config` | Deploy Home Assistant's hand-authored YAML from a source directory to HAOS |
| `homeassistant_lovelace` | Deploy Home Assistant Lovelace dashboards in YAML mode, with a drift gate |
| `homeassistant_secrets` | Manage a marked block of Home Assistant's secrets.yaml from 1Password |
| `homeassistant_update` | Update Home Assistant Core, OS or Supervisor, with backup and rollback |

## Install

```sh
ansible-galaxy collection install richard_lynch.home
```

or pin it in `requirements.yml`:

```yaml
collections:
  - name: richard_lynch.home
    version: "1.0.0"
```

Requires ansible-core >=2.19.0.

## Development

```sh
# from a checkout at ansible_collections/richard_lynch/home
ansible-test sanity --docker
ansible-test units --docker
ansible-galaxy collection build
devkit conform                  # the repository standard (ansible-devkit)
devkit check all                # the static checks, configured by devkit.toml
```

CI, the disclosure gate and releases are the shared workflows of
[ansible-devkit](https://github.com/Richard-Lynch/ansible-devkit), called from
`.github/workflows/` at tag `v0.2.1`. A release is a merged pull
request that bumps `version` in `galaxy.yml` and adds the matching
`CHANGELOG.md` section. `export.manifest` lists every tracked path; update it in
the same commit as any file added or removed.

## Licence

GPL-3.0-or-later. See [LICENSE](LICENSE).
