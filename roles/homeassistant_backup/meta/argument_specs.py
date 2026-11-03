# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations
from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec
DEFAULT_AUTHOR = "Collection maintainers"

argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="Set Home Assistant's native backup schedule via the websocket API",
            version_added="1.0",
            author=DEFAULT_AUTHOR,
            options={
                "homeassistant_backup_base_url": OptionSpec(
                    type="str",
                    required=True,
                    description=[
                        "Home Assistant base URL (http://host:8123). Installation config: set it in "
                        "inventory, never in this role."
                    ],
                ),
                "homeassistant_backup_token": OptionSpec(
                    type="str",
                    required=True,
                    no_log=True,
                    description=[
                        "Home Assistant long-lived access token, resolved from a secret store by the "
                        "caller. Never logged."
                    ],
                ),
                "homeassistant_backup_recurrence": OptionSpec(
                    type="str",
                    required=False,
                    description=["Backup schedule recurrence (daily/weekly/never)."],
                ),
                "homeassistant_backup_time": OptionSpec(
                    type="str",
                    required=False,
                    description=["Daily backup time HH:MM (appliance local)."],
                ),
                "homeassistant_backup_run_now": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description=["Trigger an automatic backup immediately after configuring."],
                ),
            },
        )
    }
)
