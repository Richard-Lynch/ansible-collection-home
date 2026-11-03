# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec
DEFAULT_AUTHOR = "Collection maintainers"

argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="Manage a marked block of Home Assistant's secrets.yaml from 1Password",
            version_added="1.0",
            author=DEFAULT_AUTHOR,
            options={
                "homeassistant_secrets_path": OptionSpec(
                    type="str",
                    required=False,
                    default="/config/secrets.yaml",
                    description=["Path to HA's secrets file inside the SSH add-on."],
                ),
                "homeassistant_secrets": OptionSpec(
                    type="list",
                    elements="dict",
                    required=False,
                    default=[],
                    description=[
                        "Which secrets to manage, as a list of:",
                        "  key:    the !secret name referenced from configuration.yaml",
                        "  item:   1Password item title (or id), read with",
                        "          richard_lynch.common.onepassword_cached",
                        "  field:  field within that item",
                        "  vault:  1Password vault (defaults to homeassistant_secrets_vault)",
                        "",
                        "EMPTY BY DEFAULT. A role that writes credentials must be told exactly which",
                        "ones; a default list would silently push secrets nobody asked for the moment",
                        "someone included the role.",
                    ],
                ),
                "homeassistant_secrets_vault": OptionSpec(
                    type="str",
                    required=False,
                    default="",
                    description=[
                        "The 1Password vault for every entry that names none. Empty by default:",
                        "which vault holds the credentials is installation config. Every entry must",
                        "resolve to a vault one way or the other.",
                    ],
                ),
                "homeassistant_secrets_apply": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description=[
                        "GENERATE-DON'T-APPLY. Default reports which keys WOULD be written and",
                        "writes nothing.",
                    ],
                ),
                "homeassistant_secrets_literal": OptionSpec(
                    type="dict",
                    required=False,
                    default={},
                    description=[
                        "Non-secret values written into the same managed block, as {key: value}.",
                        "Account identifiers and usernames belong here rather than in the vault:",
                        "putting a non-secret behind a vault lookup adds a credential fetch and a",
                        "two-field item for no confidentiality gain.",
                    ],
                ),
            },
        )
    }
)
