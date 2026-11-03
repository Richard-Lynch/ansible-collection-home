# homeassistant_backup

Set Home Assistant's **native backup schedule**. The Backup integration can have its agent,
retention and password all configured and still never run, because the **schedule** is a separate
setting that can be `never`. This role makes the schedule declared state. A native backup is what
protects the device pairings (Zigbee/Matter/HomeKit) that no configuration file can recreate.

## Why websocket (not REST)

Home Assistant OS is **a locked appliance**, so its config is API-driven. The backup schedule and
retention are settable **only over the websocket API** (`backup/config/update`); the REST API has
no service for it (`backup.create` does not exist — the only service is `backup.create_automatic`,
which itself relies on the websocket-configured schedule). So this role runs from `localhost` and
uses the collection's **`richard_lynch.home.ha_backup_config`** module (websocket) to set the schedule
idempotently. It is a collection module, not a role-local `library/` file: a role inside a
collection never loads its own `library/`.

Requires the `websocket-client` Python package on the controller.

## What it does

1. Reads the current schedule (`backup/config/info`).
2. Sets `recurrence` + `time` via `backup/config/update` **only if they differ** (idempotent;
   normalizes HA's `HH:MM:SS` against the desired `HH:MM`). Check-mode aware.
3. *(optional, `run_now: true`)* triggers `backup/generate_with_automatic_settings` — a fresh backup
   using the configured agent + retention.

## Inputs

See [`meta/argument_specs.yml`](meta/argument_specs.yml):

- `homeassistant_backup_base_url` (**required**) — e.g. `http://ha.example.test:8123`.
- `homeassistant_backup_token` (**required**, never logged) — a long-lived access token, resolved
  by the caller from its secret store.
- `homeassistant_backup_recurrence` (`daily`/`weekly`/`never`) + `homeassistant_backup_time`
  (`HH:MM`).
- `homeassistant_backup_run_now` — trigger one backup after configuring.

## Usage

```yaml
- name: Configure Home Assistant native backup
  hosts: localhost
  gather_facts: false
  roles:
    - role: richard_lynch.home.homeassistant_backup
      vars:
        homeassistant_backup_base_url: "http://ha.example.test:8123"
        homeassistant_backup_token: "{{ ha_token }}"
```

## Not in scope

- Retention tuning, and an off-box backup agent (NFS, object storage) for an offsite copy.

## Testing

`molecule/default/` runs the real role, and through it the real module, against an address with no
listener: an empty token is refused before any connection, an unknown recurrence is rejected by the
module's own argument spec, and an unreachable Home Assistant fails on the connection — never on
resolving the module. The websocket conversation itself (schedule diff, update, read-back, check
mode, `run_now`, authentication) is covered by the module's unit tests
(`tests/unit/test_ha_backup_config.py`), against a fake socket that answers like Home Assistant.
