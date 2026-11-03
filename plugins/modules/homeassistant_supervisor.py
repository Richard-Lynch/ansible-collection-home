#!/usr/bin/python
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# -*- coding: utf-8 -*-
"""Query and control the Home Assistant Supervisor from inside the SSH add-on."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: homeassistant_supervisor
short_description: Read Supervisor, add-on, host and OS state on a HAOS box
description:
  - Talks to the Home Assistant Supervisor API from a host that is INSIDE the SSH add-on container.
  - >
    The Supervisor token is read on the TARGET from
    C(/run/s6/container_environment/HASSIO_TOKEN) and never passes through the controller, a
    playbook variable or a log line. That file exists only while the add-on runs with Protection
    mode DISABLED -- with protection on, the Supervisor deliberately withholds the token and every
    request returns 401.
  - >
    This is a READ module by default. Anything that changes state requires I(action) plus
    I(apply=true): generate, don't apply.
options:
  fact:
    description: Which read to perform.
    type: str
    choices: [supervisor, addons, host, os, core, backups]
    default: supervisor
  action:
    description:
      - A state-changing operation. Requires I(apply) to actually run.
      - C(addon_restart) and C(addon_options) both need I(addon).
    type: str
    choices: [addon_restart, addon_options, core_check, core_restart, core_stop, core_start,
              core_update, backup_full, backup_partial, backup_restore, backup_remove]
  version:
    description: Target version for a C(*_update) action. Omitted means the latest available.
    type: str
  backup_name:
    description: Name for the backup a C(backup_full) or C(backup_partial) action creates.
    type: str
  backup_slug:
    description: The backup a C(backup_restore) or C(backup_remove) action acts on.
    type: str
  addon:
    description: Add-on slug, e.g. C(a0d7b954_ssh).
    type: str
  options:
    description: Options mapping to merge into the add-on's config, for I(action=addon_options).
    type: dict
  apply:
    description:
      - Must be true for I(action) to run. False reports what WOULD happen and changes nothing.
    type: bool
    default: false
  token_path:
    description: Where to read the Supervisor token on the target.
    type: str
    default: /run/s6/container_environment/HASSIO_TOKEN
  endpoint:
    description: Supervisor base URL as seen from inside the add-on.
    type: str
    default: http://supervisor
  timeout:
    description: HTTP timeout in seconds.
    type: int
    default: 30
author:
  - Richard Lynch (@Richard-Lynch)
"""

EXAMPLES = r"""
- name: Read Supervisor version and health
  richard_lynch.home.homeassistant_supervisor:
    fact: supervisor
  register: sup

- name: List add-ons and their versions
  richard_lynch.home.homeassistant_supervisor:
    fact: addons
  register: addons

- name: Preview an add-on option change (default is preview-only)
  richard_lynch.home.homeassistant_supervisor:
    action: addon_options
    addon: a0d7b954_ssh
    options:
      authorized_keys: ["ssh-ed25519 AAAA... user@example"]
"""

RETURN = r"""
data:
  description: The Supervisor API payload for the requested I(fact).
  returned: on read
  type: dict
changed:
  description: Whether a state-changing action actually ran.
  returned: always
  type: bool
"""

import json
import os

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import fetch_url
from ansible_collections.richard_lynch.home.plugins.module_utils.homeassistant import (
    build_url,
    interpret_status,
)

_READS = {
    "supervisor": "supervisor/info",
    "addons": "addons",
    "host": "host/info",
    "os": "os/info",
    "core": "core/info",
    "backups": "backups",
}


def _request(module, url, token, method="GET", body=None, timeout=30):
    headers = {"Authorization": "Bearer {0}".format(token), "Content-Type": "application/json"}
    data = json.dumps(body) if body is not None else None
    resp, info = fetch_url(module, url, headers=headers, method=method, data=data, timeout=timeout)
    status = info.get("status", -1)
    if status == -1:
        module.fail_json(
            msg="Could not reach the Supervisor at {0}: {1}".format(
                url, info.get("msg", "unknown transport error")
            )
        )
    raw = resp.read() if resp else info.get("body", b"")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    err = interpret_status(status, raw[:200] if raw else None)
    if err:
        # 401 here has ONE overwhelmingly likely cause, and saying so beats a generic auth error:
        # the add-on is running in Protection mode, so the Supervisor never issued a token.
        if status == 401:
            err += (
                " On a HAOS box this almost always means the SSH add-on is in PROTECTION MODE "
                "-- the Supervisor withholds the token entirely. Disable protection on the "
                "add-on's Info tab and restart it."
            )
        module.fail_json(msg="Supervisor request to {0} failed: {1}".format(url, err))
    try:
        return json.loads(raw) if raw else {}
    except ValueError as exc:
        module.fail_json(msg="Supervisor returned non-JSON from {0}: {1}".format(url, exc))


def main():
    module = AnsibleModule(
        argument_spec=dict(
            fact=dict(
                type="str",
                default="supervisor",
                choices=["supervisor", "addons", "host", "os", "core", "backups"],
            ),
            action=dict(
                type="str",
                choices=[
                    "addon_restart",
                    "addon_options",
                    "core_check",
                    "core_restart",
                    "core_stop",
                    "core_start",
                    "core_update",
                    "backup_full",
                    "backup_partial",
                    "backup_restore",
                    "backup_remove",
                ],
            ),
            version=dict(type="str"),
            backup_name=dict(type="str"),
            backup_slug=dict(type="str"),
            addon=dict(type="str"),
            options=dict(type="dict"),
            apply=dict(type="bool", default=False),
            token_path=dict(
                type="str", default="/run/s6/container_environment/HASSIO_TOKEN", no_log=False
            ),
            endpoint=dict(type="str", default="http://supervisor"),
            timeout=dict(type="int", default=30),
        ),
        required_if=[
            ["action", "addon_restart", ["addon"]],
            ["action", "addon_options", ["addon", "options"]],
            ["action", "backup_restore", ["backup_slug"]],
            ["action", "backup_remove", ["backup_slug"]],
        ],
        supports_check_mode=True,
    )

    p = module.params

    # THE ADD-ON'S OWN MECHANISM FIRST. /etc/profile.d/homeassistant.sh exports SUPERVISOR_TOKEN,
    # which is how the bundled `ha` CLI authenticates with no flags — but only in a LOGIN shell.
    # Ansible runs non-interactive shells, which never source profile.d, so the token appears
    # "missing" and `ha` returns 401. It was there the whole time.
    #
    # Env var first (the supported contract), then the s6 file it is derived from as a fallback for
    # contexts where profile.d has not been sourced.
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    token_source = "SUPERVISOR_TOKEN environment variable"

    if not token:
        for path in (p["token_path"], "/etc/profile.d/homeassistant.sh"):
            try:
                with open(path, "r") as fh:
                    raw = fh.read()
            except IOError:
                continue
            if path.endswith(".sh"):
                # `export SUPERVISOR_TOKEN=<value>` — take everything after the first '='.
                for line in raw.splitlines():
                    if "SUPERVISOR_TOKEN" in line and "=" in line:
                        token = line.split("=", 1)[1].strip().strip("'\"")
                        break
            else:
                token = raw.strip()
            if token:
                token_source = path
                break

    if not token:
        module.fail_json(
            msg=(
                "No Supervisor token found. Looked at: $SUPERVISOR_TOKEN, {0}, and "
                "/etc/profile.d/homeassistant.sh. Inside the SSH add-on the token exists only when "
                "Protection mode is DISABLED — with it on, the Supervisor withholds it entirely and "
                "every request returns 401.".format(p["token_path"])
            )
        )

    if not p["action"]:
        data = _request(
            module, build_url(p["endpoint"], _READS[p["fact"]]), token, timeout=p["timeout"]
        )
        module.exit_json(changed=False, data=data.get("data", data))

    # --- state-changing from here ---
    if p["action"] == "addon_restart":
        url = build_url(p["endpoint"], "addons/{0}/restart".format(p["addon"]))
        body = None
    elif p["action"] == "core_check":
        # HA's OWN verdict on the config, not a yaml-parse approximation of one. Read-only despite
        # living on the action path: it renders the config exactly as a start would and reports
        # errors without applying anything.
        url = build_url(p["endpoint"], "core/check")
        body = None
    elif p["action"] in ("core_restart", "core_stop", "core_start"):
        # Stop/start exist separately from restart for edits to .storage, which HA holds in memory
        # and rewrites on shutdown: the file must be changed while Core is DOWN, or the change is
        # silently reverted.
        url = build_url(p["endpoint"], "core/" + p["action"].split("_", 1)[1])
        body = None
    elif p["action"] == "core_update":
        # Omitting `version` lets the Supervisor pick version_latest, which is what the UI does.
        url = build_url(p["endpoint"], "core/update")
        body = {"version": p["version"]} if p["version"] else {}
    elif p["action"] in ("backup_full", "backup_partial"):
        kind = "full" if p["action"] == "backup_full" else "partial"
        url = build_url(p["endpoint"], "backups/new/" + kind)
        body = {}
        if p["backup_name"]:
            body["name"] = p["backup_name"]
        if kind == "partial":
            # A partial backup of Core + config is what a pre-update snapshot needs: it restores
            # the thing being changed without carrying gigabytes of media that the update cannot
            # touch. A full backup can be several times the size of a partial one.
            body["homeassistant"] = True
            body["folders"] = ["ssl", "share"]
    elif p["action"] == "backup_restore":
        # PARTIAL restore, Core only. A full restore would also roll back add-ons and their data,
        # which is not what "undo the Core update" means and would silently revert unrelated work.
        url = build_url(p["endpoint"], "backups/{0}/restore/partial".format(p["backup_slug"]))
        body = {"homeassistant": True}
    elif p["action"] == "backup_remove":
        url = build_url(p["endpoint"], "backups/{0}".format(p["backup_slug"]))
        body = None
    else:
        url = build_url(p["endpoint"], "addons/{0}/options".format(p["addon"]))
        body = {"options": p["options"]}

    if not p["apply"] or module.check_mode:
        module.exit_json(
            changed=False,
            would_change=True,
            url=url,
            msg=(
                "PREVIEW ONLY -- nothing was changed. Re-run with apply=true to perform "
                "'{0}' on '{1}'.".format(p["action"], p["addon"])
            ),
        )

    # core_check is a validation, so a longer timeout: HA renders the whole config, which on a
    # large instance takes well past the default.
    # core_check renders the whole config; core_restart stops and starts HA. Both routinely exceed
    # the default 30s, and a timeout there reports "Could not reach the Supervisor" for an
    # operation that is SUCCEEDING — which reads as a failure and invites a pointless retry against
    # a box mid-restart.
    # An update downloads and swaps a container image; a backup and a restore move gigabytes.
    # These are minutes, not seconds — a short timeout reports "could not reach the Supervisor" for
    # an operation that is succeeding, and invites a retry that would run it twice.
    _slow = (
        "core_check",
        "core_restart",
        "core_stop",
        "core_start",
        "core_update",
        "backup_full",
        "backup_partial",
        "backup_restore",
    )
    _very_slow = ("core_update", "backup_full", "backup_partial", "backup_restore")
    if p["action"] in _very_slow:
        tmo = max(p["timeout"], 1800)
    elif p["action"] in _slow:
        tmo = max(p["timeout"], 120)
    else:
        tmo = p["timeout"]
    method = "DELETE" if p["action"] == "backup_remove" else "POST"
    result = _request(module, url, token, method=method, body=body, timeout=tmo)

    # core_check changes nothing -- reporting changed=True for it would make every run look like a
    # mutation and defeat idempotence reporting.
    module.exit_json(
        changed=p["action"] != "core_check",
        data=(result or {}).get("data", result),
        msg="{0} succeeded{1}".format(p["action"], " on " + p["addon"] if p["addon"] else ""),
    )


if __name__ == "__main__":
    main()
