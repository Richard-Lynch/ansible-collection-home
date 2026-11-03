# `homeassistant_lovelace`

Deploys Lovelace dashboards as YAML to a HAOS box.

```yaml
- hosts: homeassistant
  roles:
    - role: richard_lynch.home.homeassistant_lovelace
      vars:
        homeassistant_lovelace_src: "{{ playbook_dir }}/files/lovelace"
        homeassistant_lovelace_dashboards:
          - { url_path: dashboard-home, file: home.yaml, title: Home, icon: "mdi:home" }
```

| Variable | Default | Meaning |
|---|---|---|
| `homeassistant_lovelace_dir` | `/config` | HA's config directory |
| `homeassistant_lovelace_src` | **required** | controller directory holding the dashboard YAML |
| `homeassistant_lovelace_subdir` | `lovelace` | must match the `filename:` paths in `configuration.yaml` |
| `homeassistant_lovelace_dashboards` | `[]` | **the registry** — drives both the files deployed and the generated `lovelace:` block |
| `homeassistant_lovelace_resources` | `[]` | custom-card resources; YAML mode ignores `.storage/lovelace_resources` |
| `homeassistant_lovelace_apply` | `false` | generate-don't-apply |
| `homeassistant_lovelace_protect` | `true` | refuse to overwrite a dashboard edited on the box |
| `homeassistant_lovelace_prune` | `false` | delete deregistered dashboard files from the box |

## Why YAML mode

In **storage mode** a dashboard is HA-owned JSON under `.storage/`, held in memory and rewritten on
shutdown — so writing it from Ansible means stopping Core first, and an edit made while HA runs is
*silently reverted*. In **YAML mode** the file is the config: plain `copy`, **no restart**, readable
diffs, comments possible.

**The trade-off: UI editing is disabled** for these dashboards. No drag-and-drop; every change is
edit → commit → deploy.

## Two path rules that are easy to get wrong

- **The default dashboard is `<config>/ui-lovelace.yaml`** — a fixed path, not a `filename:` entry.
  `filename` is a key on `dashboards:` entries *only*. Putting it under `lovelace/` makes HA report
  *"No config found"* for that one panel while the others work — a wrong path looks **partial**,
  not broken.
- **Resources must be declared in `configuration.yaml`.** In storage mode they are UI-managed
  (`.storage/lovelace_resources`); the moment `mode: yaml` is set that store is **ignored**, and any
  resource not listed stops loading — custom cards then render as an error box.

## Adding, changing and removing a dashboard

**Add:** drop the YAML in `homeassistant_lovelace_src`, add one line to
`homeassistant_lovelace_dashboards`. Deploy with this role, then restart HA (the generated block
changed, so HA needs a restart to see a NEW dashboard — edits to an existing one need none).

**Remove:** delete both. The dashboard vanishes from HA immediately; the file lingers until a run
with `homeassistant_lovelace_prune=true`, which the report names either way.

The registry is the single source of truth. A dashboard list kept in two hand-maintained places —
these files and a block in `configuration.yaml` — lets each look correct alone while the pair
disagrees.

## Drift protection

`copy` overwrites unconditionally, so an edit made on the box would be silently discarded. YAML mode
disables the UI editor, but the **vscode add-on**, a direct SSH edit or a hand-run `ha` command can
all still write these files.

The role checksums live against source before writing and **refuses to deploy** over a difference:

```
These dashboards differ on the box from what the source directory holds: lights.yaml — someone
edited them outside Ansible. Deploying would DISCARD that work. Capture the live version ... or
re-run with homeassistant_lovelace_protect=false to overwrite deliberately.
```

Capture the live file into the source directory, review it, and the deploy then proceeds.
`backup: true` also keeps a timestamped copy on the box.

## Converting

A one-shot conversion play backs up every `.storage/lovelace*` as `.pre-yaml-mode`, deploys the
YAML with this role, **asserts every referenced file landed**, then deploys the config block and
restarts. That order matters — HA does not check the files exist before honouring `mode: yaml`, so
a config-first deploy yields blank dashboards while nothing looks wrong on disk.

**To revert one dashboard:** remove its entry from the `lovelace:` block, restore
`/config/.storage/lovelace.<name>` from its `.pre-yaml-mode` backup, restart Core.

## Verifying

`lovelace/config` is **WebSocket-only** — `/api/lovelace/config` and `/api/panels` both 404 over
REST, which looks exactly like a failed conversion. Check it the way the browser does, with a
WebSocket client, and compare view counts against the originals.
