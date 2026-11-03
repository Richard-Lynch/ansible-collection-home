#!/usr/bin/python
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# -*- coding: utf-8 -*-
"""Read and converge a Canon PIXMA printer's settings through its Remote UI."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: canon_printer_settings
short_description: Read and converge Canon PIXMA settings over the Remote UI admin API
description:
  - Logs in to the printer's Remote UI as admin and reads every settings group it knows (see
    C(module_utils/canon_rui.py) C(GROUPS)), or converges the groups given in I(settings).
  - >
    A printer's configuration is a fixed set of pages, so "CRUD" here is Read and Update: there is
    nothing to create or delete. Every write is read-modify-write of the WHOLE page (the device
    requires it) followed by a read-back; the module reports what the device says AFTERWARDS.
  - >
    Generate-don't-apply: without I(apply=true) (or under C(--check)) it computes the diff against
    the LIVE values and sends nothing.
  - "Runs on the controller (C(connection: local)); the printer has no shell."
options:
  host:
    description: Printer DNS name (preferred) or address.
    type: str
    required: true
  password:
    description: Admin password. Never logged. Required unless I(status=true).
    type: str
  status:
    description:
      - Return only the UNAUTHENTICATED status (signal/link quality, error state, Wi-Fi Direct and
        DRX flags) from C(/JS_MDL/model.js). Needs no password; the cheap poll for monitoring.
    type: bool
    default: false
  ca_cert:
    description:
      - PEM path of the printer's own CA (from C(/cert_root.der)). Pins TLS to the device.
      - Required for any logged-in call unless I(insecure=true). Bootstrap it with the SDK's
        C(fetch-ca) (no credentials sent), then commit it — it is a public certificate.
    type: path
  insecure:
    description: Skip TLS verification. A deliberate one-off only; the module fails closed without it.
    type: bool
    default: false
  settings:
    description:
      - >-
        Desired state, C({group: {FIELD: value}}), e.g.
        C({wifi_power_save: {WIRELESS_INTERMITTENT: "OFF"}}).
      - Omit for a read-only report.
    type: dict
    default: {}
  groups:
    description: Which groups to REPORT. Default is every group.
    type: list
    elements: str
  new_password:
    description:
      - Rotate the admin password to this value (4-32 of [0-9A-Za-z]). Only with I(apply=true).
      - Success means the NEW password logs in and the OLD one (I(password)) is refused — checked
        with fresh sessions after the change, not inferred from the device's RW_OK.
    type: str
  apply:
    description: Must be true for I(settings) to be written. Ignored (treated as false) under C(--check).
    type: bool
    default: false
author:
  - Richard Lynch (@Richard-Lynch)
"""

EXAMPLES = r"""
- name: Report every setting
  richard_lynch.home.canon_printer_settings:
    host: printer.example.test
    password: "{{ canon_printer_password }}"
    ca_cert: "{{ canon_printer_ca_cert }}"
  register: printer

- name: Turn Wi-Fi power save off (plan unless canon_printer_apply)
  richard_lynch.home.canon_printer_settings:
    host: printer.example.test
    password: "{{ canon_printer_password }}"
    ca_cert: "{{ canon_printer_ca_cert }}"
    settings:
      wifi_power_save: {WIRELESS_INTERMITTENT: "OFF"}
    apply: "{{ canon_printer_apply | bool }}"
"""

RETURN = r"""
current:
  description: Every reported group's values AFTER any write. Secret-looking fields are redacted.
  returned: always
  type: dict
changes:
  description: Per group, the fields that differ (planned) or differed (applied), with before/after.
  returned: always
  type: dict
unsupported:
  description: Groups this model does not implement (HTTP 404 on its page).
  returned: always
  type: list
applied:
  description: Whether any write was actually sent.
  returned: unless status=true
  type: bool
status:
  description: Unauthenticated status variables (C(g_signal_strength), C(g_link_quality), ...).
  returned: when status=true
  type: dict
"""

from ansible.module_utils.basic import AnsibleModule, missing_required_lib

from ansible_collections.richard_lynch.home.plugins.module_utils.canon_rui import (
    GROUPS,
    CanonRuiClient,
    CanonRuiError,
    UnsupportedError,
    validate,
    validate_admin_password,
)

try:
    import requests  # noqa: F401  # pylint: disable=unused-import

    HAS_REQUESTS = True
except ImportError:  # pragma: no cover
    HAS_REQUESTS = False


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            password=dict(type="str", no_log=True),
            status=dict(type="bool", default=False),
            ca_cert=dict(type="path"),
            insecure=dict(type="bool", default=False),
            settings=dict(type="dict", default={}),
            new_password=dict(type="str", no_log=True),
            groups=dict(type="list", elements="str"),
            apply=dict(type="bool", default=False),
        ),
        supports_check_mode=True,
    )
    p = module.params
    apply = p["apply"] and not module.check_mode

    if p["status"]:
        if not HAS_REQUESTS:
            module.fail_json(msg=missing_required_lib("requests"))
        # Unauthenticated: signal/link quality, error state, flags. The cheap poll for monitoring.
        try:
            status = CanonRuiClient(p["host"], ca_cert=p["ca_cert"]).status()
        except Exception as exc:
            module.fail_json(msg="could not read status from %s: %s" % (p["host"], exc))
        module.exit_json(changed=False, status=status)
    if not p["password"]:
        module.fail_json(msg="password is required unless status=true")

    if p["new_password"]:
        if p["settings"]:
            module.fail_json(msg="rotate the password on its own run, not mixed with settings")
        try:
            validate_admin_password(p["new_password"])
        except CanonRuiError as exc:
            module.fail_json(msg=str(exc))
        if not apply:
            module.exit_json(
                changed=True, applied=False, rotation="PLAN: would rotate the admin password"
            )
        if not HAS_REQUESTS:
            module.fail_json(msg=missing_required_lib("requests"))
        try:
            client = CanonRuiClient(
                p["host"], p["password"], ca_cert=p["ca_cert"], insecure=p["insecure"]
            )
            res = client.change_admin_password(p["new_password"])
        except CanonRuiError as exc:
            module.fail_json(msg="rotation failed: %s" % exc, applied=True)
        except Exception as exc:
            module.fail_json(
                msg="rotation: could not talk to %s: %s" % (p["host"], exc), applied=True
            )
        module.exit_json(changed=True, applied=True, rotation=res)

    unknown = sorted(set(p["settings"]) - set(GROUPS)) + sorted(
        set(p["groups"] or []) - set(GROUPS)
    )
    if unknown:
        module.fail_json(msg="unknown group(s) %s; known: %s" % (unknown, sorted(GROUPS)))
    # Validate EVERYTHING before the first write, so a typo in the last group cannot leave the
    # printer half-converged.
    try:
        for name, desired in p["settings"].items():
            validate(GROUPS[name], desired)
    except CanonRuiError as exc:
        module.fail_json(msg=str(exc))

    # Only now is the network needed: config errors above fail with their real reason even where
    # `requests` is missing.
    if not HAS_REQUESTS:
        module.fail_json(msg=missing_required_lib("requests"))

    changes, unsupported, any_applied = {}, [], False
    try:
        with CanonRuiClient(
            p["host"], p["password"], ca_cert=p["ca_cert"], insecure=p["insecure"]
        ) as client:
            for name, desired in p["settings"].items():
                res = client.write(name, desired, apply=apply)
                if res["changes"]:
                    changes[name] = res["changes"]
                any_applied = any_applied or res["applied"]
            current = {}
            for name in p["groups"] or list(GROUPS):
                try:
                    current[name] = client.read(name)
                except UnsupportedError:
                    unsupported.append(name)
    except CanonRuiError as exc:
        module.fail_json(msg=str(exc), changes=changes, applied=any_applied)
    except Exception as exc:  # transport errors: name the host so the failure is actionable
        module.fail_json(
            msg="could not talk to %s: %s" % (p["host"], exc),
            changes=changes,
            applied=any_applied,
        )

    module.exit_json(
        changed=bool(changes),  # in plan mode this is "would change", matching --check semantics
        current=current,
        changes=changes,
        unsupported=unsupported,
        applied=any_applied,
    )


if __name__ == "__main__":
    main()
