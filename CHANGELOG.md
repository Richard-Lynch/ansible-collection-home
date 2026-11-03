<!--
Copyright: (c) 2026, Richard Lynch
SPDX-License-Identifier: GPL-3.0-or-later
-->

# Changelog

All notable changes to this collection are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the collection
uses [Semantic Versioning](https://semver.org/).

## [1.0.0] — 2026-11-03

### Added

- Roles `homeassistant_config` (hand-authored YAML with a drift gate that refuses to overwrite a live edit), `homeassistant_lovelace` (YAML-mode dashboards and their registry), `homeassistant_secrets` (a managed block of `secrets.yaml`), `homeassistant_update` (Core, OS or Supervisor update with a pre-update backup, health check and optional rollback) and `homeassistant_backup` (the native backup schedule over the websocket API).
- Role `config_zigbee_bindings` and filter `zigbee_bindings`: Zigbee bindings declared by IEEE, diffed against the live ZHA binding tables, converged additively, and pruned only behind a separate gate.
- Role `canon_printer` with modules `canon_printer_settings` and `canon_printer_maintenance` (module_utils `canon_rui`): settings drift and converge, admin-password rotation, and maintenance jobs, each a plan unless explicitly applied.
- Modules `homeassistant_info` and `homeassistant_supervisor` (module_utils `homeassistant`): read-only Home Assistant facts and Supervisor lifecycle actions.
- Every installation-specific value is an option the caller sets: addresses, tokens, config source paths, the dashboard registry and the declared bindings.
