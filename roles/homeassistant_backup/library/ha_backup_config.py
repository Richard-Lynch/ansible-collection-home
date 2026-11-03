# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Configure Home Assistant's native automatic-backup schedule via the websocket API.

HA exposes backup schedule/retention only over the websocket API
(`backup/config/info` + `backup/config/update`); the REST API cannot set it.
This module reads the current schedule, updates it only if it differs
(idempotent + check-mode aware), and can trigger an automatic backup now.
"""

from __future__ import annotations

import json
from ansible.module_utils.basic import AnsibleModule

DOCUMENTATION = r"""
---
module: ha_backup_config
short_description: Configure Home Assistant automatic-backup schedule (websocket)
description:
  - Sets the HA Backup integration schedule (recurrence/time) via the websocket API.
  - Idempotent — only updates when the desired schedule differs from the current one.
  - Optionally triggers an automatic backup immediately.
options:
  base_url: {description: [HA base URL, e.g. http://ha.example.test:8123], required: true, type: str}
  token: {description: [HA long-lived access token], required: true, type: str, no_log: true}
  recurrence: {description: [Backup schedule recurrence], choices: [daily, weekly, never], default: daily, type: str}
  time: {description: ["Time of day HH:MM (24h) for the backup"], default: "04:15", type: str}
  run_now: {description: [Trigger an automatic backup after configuring], default: false, type: bool}
  timeout: {description: [Websocket timeout seconds], default: 20, type: int}
author: [Richard Lynch]
"""

EXAMPLES = r"""
- name: Schedule a daily HA backup
  ha_backup_config:
    base_url: "http://ha.example.test:8123"
    token: "{{ ha_token }}"
    recurrence: daily
    time: "04:15"
    run_now: true
"""

RETURN = r"""
schedule: {description: [The schedule after the run], returned: always, type: dict}
"""


def _ws_url(base_url: str) -> str:
    return (
        base_url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")
        + "/api/websocket"
    )


def _connect(module, base_url, token, timeout):
    try:
        import websocket  # websocket-client
    except ImportError:
        module.fail_json(msg="The 'websocket-client' Python package is required on the controller.")
    try:
        ws = websocket.create_connection(_ws_url(base_url), timeout=timeout)
        json.loads(ws.recv())  # auth_required
        ws.send(json.dumps({"type": "auth", "access_token": token}))
        if json.loads(ws.recv()).get("type") != "auth_ok":
            module.fail_json(msg="Home Assistant websocket auth failed (bad token?).")
        return ws
    except Exception as e:  # noqa: BLE001
        module.fail_json(msg="Could not connect to HA websocket: %s" % e)


def _cmd(ws, msg_id, payload):
    payload = {"id": msg_id, **payload}
    ws.send(json.dumps(payload))
    return json.loads(ws.recv())


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
    p = module.params
    desired = {"recurrence": p["recurrence"], "time": p["time"]}

    ws = _connect(module, p["base_url"], p["token"], p["timeout"])
    try:
        info = _cmd(ws, 1, {"type": "backup/config/info"})
        cur = (info.get("result") or {}).get("config", {}).get("schedule", {}) or {}
        cur_norm = {
            "recurrence": cur.get("recurrence"),
            # HA stores time as HH:MM:SS; compare on HH:MM
            "time": (cur.get("time") or "")[:5] if cur.get("time") else None,
        }
        needs_update = cur_norm["recurrence"] != desired["recurrence"] or (
            desired["recurrence"] != "never" and cur_norm["time"] != desired["time"]
        )

        changed = False
        if needs_update and not module.check_mode:
            payload = {
                "type": "backup/config/update",
                "schedule": {"recurrence": desired["recurrence"]},
            }
            if desired["recurrence"] != "never":
                payload["schedule"]["time"] = desired["time"]
            res = _cmd(ws, 2, payload)
            if not res.get("success"):
                module.fail_json(msg="backup/config/update failed: %s" % res.get("error"))
            changed = True
        elif needs_update:
            changed = True  # check mode: would change

        if p["run_now"] and not module.check_mode:
            res = _cmd(ws, 3, {"type": "backup/generate_with_automatic_settings"})
            if not res.get("success"):
                module.fail_json(msg="triggering automatic backup failed: %s" % res.get("error"))
            changed = True

        # read back
        info2 = _cmd(ws, 4, {"type": "backup/config/info"})
        final_sched = (info2.get("result") or {}).get("config", {}).get("schedule", {})
        module.exit_json(changed=changed, schedule=final_sched)
    finally:
        try:
            ws.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
