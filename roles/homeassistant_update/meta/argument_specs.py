# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec
DEFAULT_AUTHOR = "Collection maintainers"

argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="Update Home Assistant Core, OS or Supervisor, with backup and rollback",
            version_added="1.0",
            author=DEFAULT_AUTHOR,
            options={
                "homeassistant_base_url": OptionSpec(
                    type="str",
                    required=True,
                    description=[
                        "Home Assistant base URL (http://host:8123), used for the entity-count",
                        "health check. Installation config, set by the caller.",
                    ],
                ),
                "homeassistant_token": OptionSpec(
                    type="str",
                    required=True,
                    no_log=True,
                    description=[
                        "Long-lived access token for the REST API, resolved by the caller from its",
                        "secret store. Never logged.",
                    ],
                ),
                "homeassistant_update_component": OptionSpec(
                    type="str",
                    required=False,
                    default="core",
                    choices=["core", "os", "supervisor"],
                    description=[
                        "Which component to update. `core` is Home Assistant itself; `os` and",
                        "`supervisor` are the appliance underneath it.",
                    ],
                ),
                "homeassistant_update_version": OptionSpec(
                    type="str",
                    required=False,
                    default="",
                    description=[
                        'Target version. Empty means "whatever the Supervisor reports as',
                        'version_latest", which is what the UI button does.',
                    ],
                ),
                "homeassistant_update_apply": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description=[
                        "GENERATE-DON'T-APPLY. Default reports what WOULD be updated and changes",
                        "nothing.",
                    ],
                ),
                "homeassistant_update_backup": OptionSpec(
                    type="bool",
                    required=False,
                    default=True,
                    description=[
                        "Take a partial backup (Core + config) before updating.",
                        "",
                        "ON by default and it should stay on: this is the only thing that makes the",
                        "update reversible. Partial rather than full because a full backup also",
                        "carries add-on data and media — gigabytes that a Core update cannot affect,",
                        "on an appliance disk that is often small.",
                    ],
                ),
                "homeassistant_update_min_free_mb": OptionSpec(
                    type="int",
                    required=False,
                    default=3000,
                    description=[
                        "Refuse to start if free disk is below this, in MB.",
                        "",
                        "A partial backup is typically over a gigabyte. An update that fills the disk",
                        "mid-write is far worse than one that never started: the Supervisor can be",
                        "left with a half-pulled image and no room for the rollback backup either.",
                    ],
                ),
                "homeassistant_update_settle_timeout": OptionSpec(
                    type="int",
                    required=False,
                    default=600,
                    description=[
                        "How long to wait for HA to come back and report a healthy entity count, in",
                        "seconds.",
                    ],
                ),
                "homeassistant_update_min_entities": OptionSpec(
                    type="int",
                    required=False,
                    default=0,
                    description=[
                        "Minimum entities that must be present after the update for it to count as",
                        "successful.",
                        "",
                        '0 means "derive it from the pre-update count", which is the honest default',
                        "— a fixed number goes stale as the installation grows, and a too-low one",
                        "passes a broken start. Recovery mode reports only a few dozen.",
                    ],
                ),
                "homeassistant_update_rollback": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description=[
                        "Roll back automatically if post-update validation fails.",
                        "",
                        "OFF by default, deliberately. An automatic restore is itself a risky, long",
                        "operation, and doing it unattended on a failure nobody has looked at can",
                        "turn a recoverable problem into a confusing one. The failure message names",
                        "the backup to restore instead. Set true for unattended runs.",
                    ],
                ),
            },
        )
    }
)
