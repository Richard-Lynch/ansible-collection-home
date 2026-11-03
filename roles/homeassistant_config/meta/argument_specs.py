# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec
DEFAULT_AUTHOR = "Collection maintainers"

argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="Deploy Home Assistant's hand-authored YAML from a source directory to HAOS",
            version_added="1.0",
            author=DEFAULT_AUTHOR,
            options={
                "homeassistant_config_dir": OptionSpec(
                    type="str",
                    required=False,
                    default="/config",
                    description=["Where HA's configuration lives inside the SSH add-on."],
                ),
                "homeassistant_config_src": OptionSpec(
                    type="str",
                    required=True,
                    description=[
                        "Controller directory holding the source of truth (configuration.yaml and",
                        "the rest of homeassistant_config_files/_dirs). Installation config: set it",
                        "in the play or inventory, never in this role.",
                    ],
                ),
                "homeassistant_config_files": OptionSpec(
                    type="list",
                    elements="str",
                    required=False,
                    default=[
                        "configuration.yaml",
                        "ui-lovelace.yaml",
                        "automations.yaml",
                        "scripts.yaml",
                        "scenes.yaml",
                    ],
                    description=[
                        "The files this role OWNS. Anything not listed is left alone — HA writes",
                        "plenty of state into /config that must not be reverted from a source copy",
                        "(see .storage, the recorder DB, and the UI-managed registries).",
                        "ui-lovelace.yaml is the DEFAULT dashboard (sidebar 'Overview'): in YAML",
                        "mode HA reads it from this fixed path at the config root, because",
                        "`filename` is a key on `dashboards:` entries only.",
                    ],
                ),
                "homeassistant_config_dirs": OptionSpec(
                    type="list",
                    elements="str",
                    required=False,
                    default=["templates", "blueprints"],
                    description=["Directories copied wholesale."],
                ),
                "homeassistant_config_apply": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description=[
                        "GENERATE-DON'T-APPLY. Default reports the diff and writes nothing.",
                    ],
                ),
                "homeassistant_config_protect": OptionSpec(
                    type="bool",
                    required=False,
                    default=True,
                    description=[
                        "Refuse to overwrite a file whose live copy differs from the source copy",
                        "this role last deployed.",
                        "",
                        "These files are edited through HA's UI by design — the automation editor",
                        "rewrites automations.yaml on every save — so an unconditional copy",
                        "silently DISCARDS real work and reports success while doing it.",
                        "",
                        "The source copy IS the record of what was last deployed: capturing the",
                        "live files back into it is the only sanctioned way it moves relative to",
                        "the box. So live != source means someone wrote to the box outside this",
                        "role.",
                        "",
                        "Set false to overwrite anyway — i.e. to deliberately revert a live edit.",
                        "Mirrors homeassistant_lovelace_protect; the two roles must not disagree",
                        "about whether clobbering live edits is acceptable.",
                    ],
                ),
                "homeassistant_config_restart": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description=[
                        "Restart HA Core after a change. Off by default: a restart drops every",
                        "automation mid-run and takes ~40s, so it should be a decision rather",
                        "than a side effect.",
                    ],
                ),
            },
        )
    }
)
