# `homeassistant_config`

Deploys Home Assistant's hand-authored YAML from a source directory on the controller to a HAOS
box, over SSH into the *Advanced SSH & Web Terminal* add-on.

```yaml
- hosts: homeassistant
  roles:
    - role: richard_lynch.home.homeassistant_config
      vars:
        homeassistant_config_src: "{{ playbook_dir }}/files/config"
```

| Variable | Default | Meaning |
|---|---|---|
| `homeassistant_config_dir` | `/config` | HA's config directory inside the add-on |
| `homeassistant_config_src` | **required** | controller directory holding the source of truth |
| `homeassistant_config_files` | configuration/automations/scripts/scenes `.yaml` | files this role OWNS |
| `homeassistant_config_dirs` | `templates`, `blueprints` | directories copied wholesale |
| `homeassistant_config_apply` | `false` | **generate-don't-apply**; default writes nothing |
| `homeassistant_config_protect` | `true` | refuse to overwrite a file edited on the box |
| `homeassistant_config_restart` | `false` | restart HA Core after a change |

The interface is declared in `meta/argument_specs.py`; `defaults/main.yml` and
`meta/argument_specs.yml` are generated from it.

## What it deliberately does NOT own

HA writes a lot into `/config` that a source copy must never revert:

- **`.storage/`** — UI-managed registries (integration entries, devices, entities). Editing these
  under a running HA is *silently reverted*: HA holds them in memory and rewrites them on shutdown.
- **`home-assistant_v2.db`** — the recorder database, often gigabytes.
- **`secrets.yaml`** — credentials, managed by `homeassistant_secrets` from a secret store.
- **`www/`, `custom_components/`, `notebooks/`** — media and HACS-installed code.

## Round trip, not one way

Most of these files are edited through HA's UI, so the source copy goes stale **by design**. Pull
live state back into the source directory (a capture play that fetches the owned files), review the
diff, then deploy.

Without a gate that is only a convention, and it fails the way a convention does: a deploy over a
UI edit reports `changed`, is telling the truth, and nothing reports the loss. A backup taken first
does not help — a backup you do not know to restore is not protection.

`homeassistant_config_protect` (default `true`) makes it a gate:

- **before any write**, the sha1 of each owned file on the box is compared against the source copy
- a file that differs is **drift** — someone wrote to the box outside this role
- the deploy **refuses**, naming every drifted file, and tells you to capture it first
- the report names drifted files on **both** paths, so a dry run shows them too

The source copy *is* the record of what was last deployed: capturing is the only sanctioned way it
moves relative to the box, so there is no separate manifest to go stale. Same mechanism and same
default as `homeassistant_lovelace` — the two roles write the same kind of file to the same box and
must not disagree about whether clobbering a live edit is acceptable.

To overwrite deliberately (reverting a live edit on purpose), run with
`homeassistant_config_apply=true` and `homeassistant_config_protect=false`. The drift is still
recorded and printed on that path, so the overwrite is auditable rather than silent.

## Safety properties

- **It refuses to overwrite a live edit.** See "Round trip" above.
- **The dry run really compares.** The copy tasks are not gated on `apply`; they run under
  `check_mode` instead, so a preview reports `WOULD CHANGE: <files>` or
  `nothing — live matches the source`. Gating them would make a dry run report "nothing to do" whether
  the config matched or differed wildly — a check that always returns the safe answer.
- **It refuses to write to the wrong place.** `.HA_VERSION` must exist in the target directory;
  HA writes that file and nothing else does.
- **A tar backup is taken on the target before any write**, every run. The source proves what was
  intended; the tarball proves what was actually there — and those differ exactly when it matters.
- **HA validates its own config after the write and before any restart** (`core_check` via the
  Supervisor API), so a broken config is caught before a restart takes HA down.

## Tests

`molecule/default` exercises the role's real `tasks/_drift_gate.yml` against two temp directories,
in **both directions** — a guard with only a passing test proves nothing:

| Case | Setup | Must |
|---|---|---|
| 1 | live file edited on the box | **refuse**, naming `automations.yaml` |
| 2 | live file identical to the source | deploy normally, report no drift |
| 3 | drifted, `protect=false` | allow the overwrite, still record the drift |
| 4 | drifted, dry run | complete (nothing is written) and still report the drift |

No Home Assistant is touched: the gate runs before the tar backup, the copy and `core_check`, so
entering at `tasks_from: _drift_gate` reaches all of the code under test and none of the I/O.

The collection's unit tests (`tests/unit/test_homeassistant.py`) cover the Supervisor module's
endpoint table.
