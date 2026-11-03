# SPDX-License-Identifier: GPL-3.0-or-later
from __future__ import annotations

from ansible_devkit.argspec.models import ArgumentSpec, ArgumentSpecs, OptionSpec
DEFAULT_AUTHOR = "Collection maintainers"

argument_specs = ArgumentSpecs(
    argument_specs={
        "main": ArgumentSpec(
            short_description="Manage a Canon PIXMA printer through its Remote UI admin API",
            version_added="1.0",
            author=DEFAULT_AUTHOR,
            options={
                "canon_printer_action": OptionSpec(
                    type="str",
                    required=False,
                    default="read",
                    choices=[
                        "probe",
                        "read",
                        "converge",
                        "rotate_password",
                        "sync_password",
                        "nozzle_check",
                        "cleaning",
                        "deep_cleaning",
                        "power_off",
                    ],
                    description=(
                        "probe: unauthenticated status (signal, link quality, error state). "
                        "read: log in and report every settings group. "
                        "converge: diff canon_printer_settings against the live printer; writes only "
                        "with canon_printer_apply=true. "
                        "rotate_password: new random admin password, 1Password first, then the "
                        "device, proven by login; needs canon_printer_rotate_confirm=true. "
                        "sync_password: make 1Password (URL), the credentials cache and the device "
                        "agree — idempotent; finishes a rotation that stopped part-way. "
                        "nozzle_check / cleaning / deep_cleaning: run that Maintenance-menu job and "
                        "wait for it to finish — uses paper and/or ink, so it is a PLAN unless "
                        "canon_printer_apply=true. "
                        "power_off: soft power-off (caps the print head, as the ON button does); "
                        "PLAN unless canon_printer_apply=true. The printer then needs its ON button."
                    ),
                ),
                "canon_printer_host": OptionSpec(
                    type="str",
                    required=True,
                    description=(
                        "DNS name of the printer. Installation config, set in inventory. Prefer a "
                        "name over an IP: the device's address is the DHCP server's to change."
                    ),
                ),
                "canon_printer_password": OptionSpec(
                    type="str",
                    required=False,
                    no_log=True,
                    description=(
                        "Remote UI admin password (typically a lazy 1Password lookup by item ID). "
                        "Required for read and converge; probe needs none."
                    ),
                ),
                "canon_printer_ca_cert": OptionSpec(
                    type="str",
                    required=False,
                    description=(
                        "PEM path of the printer's own CA (served at /cert_root.der). Pins TLS to the "
                        "device. Unset means NO verification — first contact only."
                    ),
                ),
                "canon_printer_settings": OptionSpec(
                    type="dict",
                    required=False,
                    default={},
                    description=(
                        "Desired settings as {group: {FIELD: value}}; groups and writable fields are "
                        "defined in module_utils/canon_rui.py GROUPS. Installation config — set in "
                        "inventory, never in this role."
                    ),
                ),
                "canon_printer_password_item": OptionSpec(
                    type="str",
                    required=False,
                    description=(
                        "1Password item ID holding the admin password (by ID, which also works for a "
                        "title containing slashes). rotate_password rolls it and refreshes the "
                        "credentials cache."
                    ),
                ),
                "canon_printer_admin_url": OptionSpec(
                    type="str",
                    required=False,
                    description=(
                        "Website recorded on the 1Password item so 1Password autofills the Remote UI "
                        "login. Installation config."
                    ),
                ),
                "canon_printer_vault": OptionSpec(
                    type="str",
                    required=False,
                    description=(
                        "1Password vault holding canon_printer_password_item. Required by "
                        "rotate_password and sync_password; installation config."
                    ),
                ),
                "canon_printer_cleaning_pattern": OptionSpec(
                    type="str",
                    required=False,
                    choices=["ALL", "PAT1", "PAT2"],
                    description=(
                        "Ink group for cleaning/deep_cleaning, sent as the UI's CLN_PATTERN. Unset "
                        "sends the kick with no body, as the UI's own nor_cleaning() does (every "
                        "group). Set it in a vars file (-e @file.yml), not on the command line."
                    ),
                ),
                "canon_printer_rotate_confirm": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description="Gate for rotate_password (the only flag that may be passed on the CLI).",
                ),
                "canon_printer_apply": OptionSpec(
                    type="bool",
                    required=False,
                    default=False,
                    description=(
                        "Generate-don't-apply: converge and the maintenance/power actions report "
                        "the plan unless this is true."
                    ),
                ),
            },
        ),
    }
)
