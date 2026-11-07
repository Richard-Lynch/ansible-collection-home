<!--
Copyright: (c) 2026, Richard Lynch
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Changelog

All notable changes to this collection are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the collection
uses [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-11-07

### Added

- **`module_utils/ha_websocket.py`** — a minimal authenticated Home Assistant WebSocket client
  (`HomeAssistantWS` as a context manager, `ws_url`, `token_from_stdin`), for the parts of HA that
  are reachable only over the WebSocket API (the entity registry, zones and helpers). Every
  handshake step is checked, commands are correlated by id through interleaved event frames, and
  the token is read from stdin, never argv. Moved from the IaaC hub's Home Assistant maintenance
  scripts (M6 batch 5), which import it from here; unit tests against a scripted fake socket.

## [1.0.1] — 2026-11-03

### Fixed

- `ha_backup_config` is now a collection module (`richard_lynch.home.ha_backup_config`). It shipped
  in the `homeassistant_backup` role's own `library/`, which Ansible never loads for a role inside a
  collection, so the role failed at its first task with "couldn't resolve module/action". The role
  now calls the module by its fully qualified name.
- `ha_backup_config` checks every websocket reply's id and success, and fails when the schedule
  read back after an update still differs from the one requested. `run_now` triggers the backup
  only after that read-back, so a backup never runs under a schedule that did not apply.
- `ha_backup_config` in check mode reports a change when `run_now` is set, as a real run does.
- `ha_backup_config` validates `time` as `HH:MM` (24-hour) before connecting, unless the
  recurrence is `never`.
- Every role carries a `meta/main.yml` with a Galaxy description.
- Every filter in `plugins/filter/` has `ansible-doc` documentation (one sidecar `.yml` per
  filter name), so the Galaxy import reports no documentation error.

### Added

- Module documentation for `ha_backup_config` (options, examples, return values, check-mode
  support), and unit tests that run it against a fake Home Assistant websocket.

### Changed

- The `homeassistant_backup` molecule scenario runs the real role and module (an empty token, an
  unknown recurrence, a malformed time, an unreachable host) instead of re-deriving the module's
  schedule comparison.
- CI, disclose and release callers use ansible-devkit v0.2.3.

## [1.0.0] — 2026-11-03

### Added

- Roles `homeassistant_config` (hand-authored YAML with a drift gate that refuses to overwrite a live edit), `homeassistant_lovelace` (YAML-mode dashboards and their registry), `homeassistant_secrets` (a managed block of `secrets.yaml`), `homeassistant_update` (Core, OS or Supervisor update with a pre-update backup, health check and optional rollback) and `homeassistant_backup` (the native backup schedule over the websocket API).
- Role `config_zigbee_bindings` and filter `zigbee_bindings`: Zigbee bindings declared by IEEE, diffed against the live ZHA binding tables, converged additively, and pruned only behind a separate gate.
- Role `canon_printer` with modules `canon_printer_settings` and `canon_printer_maintenance` (module_utils `canon_rui`): settings drift and converge, admin-password rotation, and maintenance jobs, each a plan unless explicitly applied.
- Modules `homeassistant_info` and `homeassistant_supervisor` (module_utils `homeassistant`): read-only Home Assistant facts and Supervisor lifecycle actions.
- Every installation-specific value is an option the caller sets: addresses, tokens, config source paths, the dashboard registry and the declared bindings.
