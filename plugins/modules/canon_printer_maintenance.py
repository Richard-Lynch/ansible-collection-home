#!/usr/bin/python
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
"""Run a Canon PIXMA maintenance job, or power the printer off, over its network APIs."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: canon_printer_maintenance
short_description: Run Canon PIXMA maintenance (nozzle check, cleaning) or a soft power-off
description:
  - >
    Runs one of the printer's own Maintenance-menu jobs through the Remote UI as admin (see
    C(module_utils/canon_rui.py) C(MAINTENANCE)) and waits for the device to report it finished;
    or sends a soft power-off over CHMP and confirms the printer then stops answering.
  - >
    These are ACTIONS, not state: running one twice does it twice, and the jobs use ink and paper.
    Without I(apply=true) (or under C(--check)) nothing is sent and the module reports the plan,
    with C(changed=false) — nothing changed. An applied run always reports C(changed=true).
  - >
    The soft power-off is the same shutdown as the ON button, so the print head is capped (parked).
    Pulling the power instead can leave it uncapped and drying (Canon TS8700 manual, "Turning the
    Printer On and Off").
  - "Runs on the controller (C(connection: local)); the printer has no shell."
options:
  host:
    description: Printer DNS name (preferred) or address.
    type: str
    required: true
  job:
    description: What to run.
    type: str
    required: true
    choices: [nozzle_check, cleaning, deep_cleaning, power_off]
  pattern:
    description:
      - Which ink group a C(cleaning)/C(deep_cleaning) cleans (C(ALL), C(PAT1), C(PAT2)), sent as
        the UI's C(CLN_PATTERN). Omit to send the kick with no body, as the UI's own
        C(nor_cleaning()) does (cleans every group).
    type: str
    choices: [ALL, PAT1, PAT2]
  password:
    description: Admin password, for the jobs. Never logged. C(power_off) needs none.
    type: str
  ca_cert:
    description: PEM path of the printer's own CA. Required for the jobs (the login is pinned).
    type: path
  apply:
    description: Must be true for anything to be sent. Ignored (treated as false) under C(--check).
    type: bool
    default: false
author:
  - Richard Lynch (@Richard-Lynch)
"""

EXAMPLES = r"""
- name: Print a nozzle check pattern
  richard_lynch.home.canon_printer_maintenance:
    host: printer.example.test
    job: nozzle_check
    password: "{{ canon_printer_password }}"
    ca_cert: "{{ canon_printer_ca_cert }}"
    apply: true

- name: Power the printer off properly (caps the print head)
  richard_lynch.home.canon_printer_maintenance:
    host: printer.example.test
    job: power_off
    apply: true
"""

RETURN = r"""
applied:
  description: Whether anything was sent to the printer.
  returned: always
  type: bool
plan:
  description: What would be sent, and what it costs. Only when nothing was sent.
  returned: when not applied
  type: str
result:
  description: >
    For a job, C({job, result: done, job_num, seconds}) once the device reports it finished. For
    power_off, C({sent, off_after_s}): the reply to the command and how long until the printer
    stopped answering.
  returned: when applied
  type: dict
"""

from ansible.module_utils.basic import AnsibleModule, missing_required_lib
from ansible_collections.richard_lynch.home.plugins.module_utils.canon_rui import (
    MAINTENANCE,
    CanonRuiClient,
    CanonRuiError,
    maintenance_body,
    power_off,
)

try:
    import requests  # noqa: F401  # pylint: disable=unused-import

    HAS_REQUESTS = True
except ImportError:  # pragma: no cover
    HAS_REQUESTS = False

POWER_OFF_PLAN = (
    "would send a soft power-off (CHMP PowerOff): the printer caps its print head and shuts down, "
    "leaves the network, and needs its ON button to come back"
)


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            job=dict(
                type="str",
                required=True,
                choices=sorted(MAINTENANCE) + ["power_off"],
            ),
            pattern=dict(type="str", choices=["ALL", "PAT1", "PAT2"]),
            password=dict(type="str", no_log=True),
            ca_cert=dict(type="path"),
            apply=dict(type="bool", default=False),
        ),
        supports_check_mode=True,
    )
    p = module.params
    apply = p["apply"] and not module.check_mode

    if p["job"] == "power_off":
        if p["pattern"]:
            module.fail_json(msg="power_off takes no cleaning pattern")
        if not apply:
            module.exit_json(changed=False, applied=False, plan=POWER_OFF_PLAN)
        if not HAS_REQUESTS:
            module.fail_json(msg=missing_required_lib("requests"))
        try:
            res = power_off(p["host"])
        except CanonRuiError as exc:
            module.fail_json(msg=str(exc))
        except Exception as exc:
            module.fail_json(msg="power_off: could not talk to %s: %s" % (p["host"], exc))
        module.exit_json(changed=True, applied=True, result=res)

    job = MAINTENANCE[p["job"]]
    # Validate before anything else, so a bad pattern fails with its reason even in plan mode.
    try:
        maintenance_body(job, p["pattern"])
    except CanonRuiError as exc:
        module.fail_json(msg=str(exc))
    if not apply:
        module.exit_json(
            changed=False, applied=False, plan="would run %s: %s" % (job.name, job.description)
        )
    if not p["password"] or not p["ca_cert"]:
        module.fail_json(
            msg="%s needs password and ca_cert (the admin login is only sent over pinned TLS)"
            % job.name
        )
    if not HAS_REQUESTS:
        module.fail_json(msg=missing_required_lib("requests"))
    try:
        with CanonRuiClient(p["host"], p["password"], ca_cert=p["ca_cert"]) as client:
            res = client.run_job(job.name, p["pattern"])
    except CanonRuiError as exc:
        module.fail_json(msg=str(exc))
    except Exception as exc:  # transport errors: name the host so the failure is actionable
        module.fail_json(msg="could not talk to %s: %s" % (p["host"], exc))
    module.exit_json(changed=True, applied=True, result=res)


if __name__ == "__main__":
    main()
