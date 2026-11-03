#!/usr/bin/python
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# -*- coding: utf-8 -*-
"""Configure Home Assistant's native automatic-backup schedule over the websocket API."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: ha_backup_config
short_description: Set the Home Assistant automatic-backup schedule over the websocket API
description:
  - Sets the schedule (recurrence and time of day) of the Home Assistant Backup integration.
  - >
    The schedule is settable only over the websocket API (C(backup/config/info) and
    C(backup/config/update)); the REST API has no service for it.
  - >
    Idempotent. The current schedule is read first and updated only when it differs from the
    requested one. Home Assistant stores the time as C(HH:MM:SS); it is compared on C(HH:MM). With
    I(recurrence=never) the time is not compared.
  - Optionally triggers one automatic backup, with the configured agents and retention, after configuring.
  - Runs on the host that can reach Home Assistant, normally the controller.
author:
  - Richard Lynch (@Richard-Lynch)
requirements:
  - websocket-client
options:
  base_url:
    description:
      - Base URL of Home Assistant, for example C(http://ha.example.test:8123).
      - C(http) becomes C(ws) and C(https) becomes C(wss); C(/api/websocket) is appended.
    type: str
    required: true
  token:
    description: A Home Assistant long-lived access token.
    type: str
    required: true
  recurrence:
    description: How often the automatic backup runs.
    type: str
    choices: [daily, weekly, never]
    default: daily
  time:
    description:
      - Time of day for the backup, C(HH:MM) in 24-hour form (C(00:00) to C(23:59)).
      - Validated before any connection. Ignored when I(recurrence=never).
    type: str
    default: "04:15"
  run_now:
    description:
      - Trigger an automatic backup after configuring the schedule.
      - Runs only after the schedule has been read back and matches.
      - Always reports C(changed), in check mode too, where the backup is not triggered.
    type: bool
    default: false
  timeout:
    description: Websocket connect and receive timeout, in seconds.
    type: int
    default: 20
attributes:
  check_mode:
    support: full
    description: Reports whether the schedule would change; never writes it and never triggers a backup.
  diff_mode:
    support: none
    description: The schedule before and after is not returned as a diff; see RV(schedule).
notes:
  - The token is sent only inside the websocket authentication message and is never returned.
"""

EXAMPLES = r"""
- name: Schedule a daily backup at 04:15
  richard_lynch.home.ha_backup_config:
    base_url: "http://ha.example.test:8123"
    token: "{{ ha_token }}"
    recurrence: daily
    time: "04:15"

- name: Turn the automatic schedule off
  richard_lynch.home.ha_backup_config:
    base_url: "http://ha.example.test:8123"
    token: "{{ ha_token }}"
    recurrence: never

- name: Configure, then take a backup now
  richard_lynch.home.ha_backup_config:
    base_url: "https://ha.example.test"
    token: "{{ ha_token }}"
    run_now: true
"""

RETURN = r"""
schedule:
  description:
    - The schedule read back from Home Assistant after the run.
    - In check mode, the schedule as it currently is (nothing was written).
  returned: success
  type: dict
  sample: {"recurrence": "daily", "time": "04:15:00", "days": []}
backup_triggered:
  description: Whether an automatic backup was triggered.
  returned: success
  type: bool
  sample: false
"""

import json
import re
import traceback

from ansible.module_utils.basic import AnsibleModule, missing_required_lib

WEBSOCKET_IMPORT_ERROR = None
try:
    import websocket  # websocket-client
except ImportError:
    websocket = None
    WEBSOCKET_IMPORT_ERROR = traceback.format_exc()


TIME_RE = re.compile(r"\A(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


class BackupConfigError(Exception):
    """A websocket exchange failed; the message is safe to show (it never carries the token)."""


def ws_url(base_url):
    """The websocket endpoint for a Home Assistant base URL."""
    url = base_url.strip().rstrip("/")
    scheme, sep, rest = url.partition("://")
    if sep and scheme in ("http", "https"):
        url = ("wss" if scheme == "https" else "ws") + "://" + rest
    return url + "/api/websocket"


def normalise_schedule(schedule):
    """Home Assistant's schedule reduced to the fields compared: recurrence, and time as HH:MM."""
    schedule = schedule or {}
    time = schedule.get("time")
    return {
        "recurrence": schedule.get("recurrence"),
        "time": time[:5] if time else None,
    }


def schedule_needs_update(current, desired):
    """True when the live schedule differs from the requested one. The time matters unless `never`."""
    cur = normalise_schedule(current)
    if cur["recurrence"] != desired["recurrence"]:
        return True
    return desired["recurrence"] != "never" and cur["time"] != desired["time"]


def update_payload(desired):
    """The `backup/config/update` message for the requested schedule."""
    schedule = {"recurrence": desired["recurrence"]}
    if desired["recurrence"] != "never":
        schedule["time"] = desired["time"]
    return {"type": "backup/config/update", "schedule": schedule}


class Session:
    """One authenticated websocket conversation; each command gets the next message id."""

    def __init__(self, ws):
        self.ws = ws
        self.next_id = 1

    def authenticate(self, token):
        first = json.loads(self.ws.recv())
        if first.get("type") != "auth_required":
            raise BackupConfigError("unexpected first websocket message: %s" % first.get("type"))
        self.ws.send(json.dumps({"type": "auth", "access_token": token}))
        reply = json.loads(self.ws.recv())
        if reply.get("type") != "auth_ok":
            raise BackupConfigError("Home Assistant websocket authentication failed (bad token?)")

    def command(self, payload):
        msg_id = self.next_id
        self.next_id += 1
        self.ws.send(json.dumps(dict(payload, id=msg_id)))
        reply = json.loads(self.ws.recv())
        if reply.get("id") != msg_id:
            raise BackupConfigError(
                "websocket reply id %s does not match request %s" % (reply.get("id"), msg_id)
            )
        if not reply.get("success"):
            raise BackupConfigError("%s failed: %s" % (payload["type"], reply.get("error")))
        return reply.get("result") or {}

    def schedule(self):
        result = self.command({"type": "backup/config/info"})
        return (result.get("config") or {}).get("schedule") or {}


def configure(session, desired, run_now, check_mode):
    """Converge the schedule. Returns (changed, schedule after the run, backup triggered)."""
    current = session.schedule()
    needs_update = schedule_needs_update(current, desired)
    if check_mode:
        return needs_update or run_now, current, False
    final = current
    if needs_update:
        session.command(update_payload(desired))
        final = session.schedule()
        if schedule_needs_update(final, desired):
            raise BackupConfigError(
                "the schedule read back after the update still differs: %s" % final
            )
    # Only once the schedule is proven, so a backup never runs under a schedule that did not apply.
    if run_now:
        session.command({"type": "backup/generate_with_automatic_settings"})
    return needs_update or run_now, final, run_now


def main():
    module = AnsibleModule(
        argument_spec=dict(
            base_url=dict(type="str", required=True),
            token=dict(type="str", required=True, no_log=True),
            recurrence=dict(type="str", default="daily", choices=["daily", "weekly", "never"]),
            time=dict(type="str", default="04:15"),
            run_now=dict(type="bool", default=False),
            timeout=dict(type="int", default=20),
        ),
        supports_check_mode=True,
    )
    if websocket is None:
        module.fail_json(
            msg=missing_required_lib("websocket-client"),
            exception=WEBSOCKET_IMPORT_ERROR,
        )
    p = module.params
    if p["recurrence"] != "never" and not TIME_RE.match(p["time"]):
        module.fail_json(msg="time must be HH:MM in 24-hour form, got %r" % p["time"])
    desired = {"recurrence": p["recurrence"], "time": p["time"]}
    try:
        ws = websocket.create_connection(ws_url(p["base_url"]), timeout=p["timeout"])
    except Exception as e:  # websocket-client raises socket, SSL and handshake errors alike
        module.fail_json(msg="could not connect to the Home Assistant websocket: %s" % e)
    try:
        session = Session(ws)
        session.authenticate(p["token"])
        changed, schedule, triggered = configure(session, desired, p["run_now"], module.check_mode)
    except BackupConfigError as e:
        module.fail_json(msg=str(e))
    except Exception as e:  # a dropped connection mid-conversation
        module.fail_json(msg="Home Assistant websocket conversation failed: %s" % e)
    finally:
        try:
            ws.close()
        except Exception:  # closing a dead socket is not a failure of the run
            pass
    module.exit_json(changed=changed, schedule=schedule, backup_triggered=triggered)


if __name__ == "__main__":
    main()
