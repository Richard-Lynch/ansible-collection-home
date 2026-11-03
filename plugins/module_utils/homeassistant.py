# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared Home Assistant REST helpers.

WHY A MODULE AND NOT MORE `uri` TASKS. A hand-rolled bearer-token `uri` call has to re-derive the
base URL, re-assemble the Authorization header, and decide for itself what a failure looks like --
and separate copies decide slightly differently. A shared surface makes the auth and the error
handling one thing to get right.

HAOS IS API-ONLY FROM A CONTROLLER'S POINT OF VIEW. Its SSH belongs to an add-on rather than the
host, and the OS is immutable, so no agent survives an update. The REST API is not a convenience
here, it is the entire management surface.

The parsing/decision logic lives in plain functions so it can be unit-tested without a live HA or
an AnsibleModule instance. Everything that touches the network stays in the module proper.
"""

from __future__ import annotations


class HomeAssistantError(Exception):
    """A Home Assistant API interaction failed in a way the caller must handle."""


def build_url(base_url, path):
    """Join a base URL and an API path without doubling or dropping the slash.

    `urljoin` is deliberately NOT used: it treats a path as absolute and DISCARDS any existing path
    on the base, so a base of `http://ha:8123/subpath` silently loses `/subpath`. That failure is
    invisible in the resulting string until a request 404s somewhere unexpected.
    """
    if not base_url:
        raise HomeAssistantError("base_url is required")
    return "{0}/{1}".format(base_url.rstrip("/"), str(path).lstrip("/"))


def auth_headers(token, extra=None):
    """Build the Authorization header set for a long-lived access token."""
    if not token:
        raise HomeAssistantError("token is required")
    headers = {
        "Authorization": "Bearer {0}".format(token),
        "Content-Type": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def interpret_status(status, body=None):
    """Turn an HTTP status into either None (fine) or a precise, actionable error message.

    Each case is separated because the FIXES differ, and an operator reading a failed play needs to
    know which one they are in. A single "HA request failed" would collapse a wrong token, an
    expired token and a missing integration into one indistinguishable message.
    """
    if status in (200, 201):
        return None
    if status == 401:
        return (
            "401 Unauthorized -- the long-lived access token was rejected. HA tokens do not "
            "expire on a schedule but ARE invalidated when the owning user's password changes "
            "or the token is revoked in the UI."
        )
    if status == 403:
        return (
            "403 Forbidden -- authenticated but not permitted. A plain long-lived token cannot "
            "reach the Supervisor API (/api/hassio/*); that needs a separate Supervisor token."
        )
    if status == 404:
        return (
            "404 Not Found -- the endpoint does not exist on this instance. For /api/prometheus "
            "this means the `prometheus:` integration is not enabled in configuration.yaml, "
            "not that the host is wrong."
        )
    return "HTTP {0}{1}".format(status, ": {0}".format(body) if body else "")


def entity_is_available(state):
    """Whether an entity state counts as available.

    HA reports BOTH `unavailable` (the integration cannot reach the device) and `unknown` (it is
    reachable but has never reported a value). Treating only `unavailable` as down silently counts
    every never-reporting entity as healthy -- and a long-lived instance can carry hundreds of permanently
    unavailable entities, so that error would be large and invisible.
    """
    return str(state).lower() not in ("unavailable", "unknown")


def summarise_states(states):
    """Reduce /api/states output to counts, tolerating malformed rows rather than crashing.

    A single bad row must not abort a whole health snapshot -- the point of the snapshot is to be
    runnable at any time, including while something is broken.
    """
    total = available = unavailable = 0
    by_domain = {}
    for item in states or []:
        if not isinstance(item, dict):
            continue
        entity_id = item.get("entity_id")
        if not entity_id:
            continue
        total += 1
        domain = str(entity_id).split(".", 1)[0]
        bucket = by_domain.setdefault(domain, {"total": 0, "available": 0})
        bucket["total"] += 1
        if entity_is_available(item.get("state")):
            available += 1
            bucket["available"] += 1
        else:
            unavailable += 1
    return {
        "total": total,
        "available": available,
        "unavailable": unavailable,
        "by_domain": by_domain,
    }
