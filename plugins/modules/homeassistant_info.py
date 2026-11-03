#!/usr/bin/python
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# -*- coding: utf-8 -*-
"""Read state from a Home Assistant instance over its REST API."""

from __future__ import annotations

DOCUMENTATION = r"""
---
module: homeassistant_info
short_description: Read entity state and instance info from Home Assistant
description:
  - Queries a Home Assistant instance over its REST API and returns entity state.
  - >
    HAOS is an appliance whose SSH belongs to an add-on rather than the host, and whose OS is
    immutable, so no agent survives an update. The REST API is therefore the whole management
    surface, and this module is how plays reach it in an Ansible-native way instead of hand-rolling
    a bearer-token C(uri) call each time.
  - This module only reads. It never changes state and is safe under C(--check).
options:
  base_url:
    description: Base URL of the Home Assistant instance, e.g. C(http://ha.example.test:8123).
    required: true
    type: str
  token:
    description:
      - Long-lived access token.
      - Resolve it from a secret store at run time; it is never logged.
    required: true
    type: str
  fact:
    description:
      - What to fetch. C(api) is a cheap liveness check, C(states) returns every entity,
        C(config) returns instance configuration.
    type: str
    choices: [api, states, config]
    default: states
  domain:
    description: Restrict returned entities to a single domain, e.g. C(light) or C(sensor).
    type: str
  timeout:
    description: HTTP timeout in seconds.
    type: int
    default: 30
  validate_certs:
    description:
      - Whether to validate TLS certificates.
      - >
        Defaults to true. Over plain HTTP this setting is not consulted at all; it matters only if
        the instance is fronted by TLS.
    type: bool
    default: true
author:
  - Richard Lynch (@Richard-Lynch)
"""

EXAMPLES = r"""
- name: Confirm Home Assistant is answering
  richard_lynch.home.homeassistant_info:
    base_url: "{{ ha_base_url }}"
    token: "{{ ha_token }}"
    fact: api
  register: ha

- name: Count unavailable ESPHome entities
  richard_lynch.home.homeassistant_info:
    base_url: "{{ ha_base_url }}"
    token: "{{ ha_token }}"
    fact: states
    domain: sensor
  register: sensors

- name: Fail if availability regressed against the baseline
  ansible.builtin.assert:
    that:
      - "sensors.summary.available >= baseline.available"
"""

RETURN = r"""
summary:
  description: Entity counts. Only populated when I(fact=states).
  returned: when fact=states
  type: dict
  sample: {"total": 120, "available": 110, "unavailable": 10, "by_domain": {}}
entities:
  description: The raw entity list, filtered by I(domain) when given.
  returned: when fact=states
  type: list
  elements: dict
data:
  description: Raw response body for I(fact=api) and I(fact=config).
  returned: when fact=api or fact=config
  type: dict
"""

import json

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.urls import fetch_url
from ansible_collections.richard_lynch.home.plugins.module_utils.homeassistant import (
    auth_headers,
    build_url,
    interpret_status,
    summarise_states,
)

_PATHS = {"api": "api/", "states": "api/states", "config": "api/config"}


def main():
    module = AnsibleModule(
        argument_spec=dict(
            base_url=dict(type="str", required=True),
            token=dict(type="str", required=True, no_log=True),
            fact=dict(type="str", default="states", choices=["api", "states", "config"]),
            domain=dict(type="str"),
            timeout=dict(type="int", default=30),
            validate_certs=dict(type="bool", default=True),
        ),
        # Read-only by construction, so it is honest under --check rather than skipping and
        # leaving the caller with no data to assert on.
        supports_check_mode=True,
    )

    p = module.params
    url = build_url(p["base_url"], _PATHS[p["fact"]])

    resp, info = fetch_url(
        module,
        url,
        headers=auth_headers(p["token"]),
        method="GET",
        timeout=p["timeout"],
    )

    status = info.get("status", -1)
    # fetch_url signals transport failures with status -1 and puts the reason in info['msg'];
    # there is no response object to read in that case.
    if status == -1:
        module.fail_json(
            msg="Could not reach Home Assistant at {0}: {1}".format(
                url, info.get("msg", "unknown transport error")
            )
        )

    body = resp.read() if resp else info.get("body", b"")
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")

    err = interpret_status(status, body[:200] if body else None)
    if err:
        module.fail_json(msg="Home Assistant request to {0} failed: {1}".format(url, err))

    try:
        parsed = json.loads(body) if body else {}
    except ValueError as exc:
        module.fail_json(msg="Home Assistant returned non-JSON from {0}: {1}".format(url, exc))

    result = {"changed": False}
    if p["fact"] == "states":
        entities = parsed if isinstance(parsed, list) else []
        if p["domain"]:
            prefix = "{0}.".format(p["domain"])
            entities = [
                e
                for e in entities
                if isinstance(e, dict) and str(e.get("entity_id", "")).startswith(prefix)
            ]
        result["entities"] = entities
        result["summary"] = summarise_states(entities)
    else:
        result["data"] = parsed

    module.exit_json(**result)


if __name__ == "__main__":
    main()
