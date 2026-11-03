# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the Home Assistant module_utils.

Tests the PURE LOGIC only -- no live HA, no AnsibleModule. Everything asserted here is a decision
the module makes about data it was handed, which is exactly the part that can be wrong silently.

Run: python -m pytest tests/unit -q (from the collection root)
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "module_utils"))

from homeassistant import (  # noqa: E402
    HomeAssistantError,
    auth_headers,
    build_url,
    entity_is_available,
    interpret_status,
    summarise_states,
)


class TestBuildUrl:
    def test_joins_without_doubling_slash(self):
        assert build_url("http://ha:8123/", "/api/states") == "http://ha:8123/api/states"

    def test_joins_when_neither_side_has_a_slash(self):
        assert build_url("http://ha:8123", "api/states") == "http://ha:8123/api/states"

    def test_preserves_a_base_path(self):
        # The reason urljoin is not used: it would DISCARD /ha here and silently produce
        # http://host/api/states, which 404s somewhere far from the cause.
        assert build_url("http://host/ha", "/api/states") == "http://host/ha/api/states"

    def test_empty_base_is_an_error_not_a_relative_url(self):
        with pytest.raises(HomeAssistantError):
            build_url("", "/api/")


class TestAuthHeaders:
    def test_sets_bearer_and_content_type(self):
        h = auth_headers("tok")
        assert h["Authorization"] == "Bearer tok"
        assert h["Content-Type"] == "application/json"

    def test_extra_headers_merge(self):
        assert auth_headers("tok", {"X-Trace": "1"})["X-Trace"] == "1"

    def test_empty_token_raises_rather_than_sending_bearer_none(self):
        # Sending `Bearer ` would come back 401 and read as a wrong token rather than a missing one.
        with pytest.raises(HomeAssistantError):
            auth_headers("")


class TestInterpretStatus:
    def test_success_returns_no_error(self):
        assert interpret_status(200) is None
        assert interpret_status(201) is None

    @pytest.mark.parametrize(
        "status,needle",
        [
            (401, "token was rejected"),
            (403, "Supervisor"),
            (404, "prometheus"),
        ],
    )
    def test_each_failure_names_its_own_cause(self, status, needle):
        # The whole point of separating these: the fixes differ, so the messages must too.
        msg = interpret_status(status)
        assert msg is not None and needle in msg

    def test_unmapped_status_still_reports_the_code(self):
        assert "503" in interpret_status(503, "upstream down")


class TestEntityAvailability:
    @pytest.mark.parametrize("state", ["on", "off", "23.5", "home", "idle"])
    def test_real_states_are_available(self, state):
        assert entity_is_available(state) is True

    @pytest.mark.parametrize("state", ["unavailable", "unknown", "UNAVAILABLE", "Unknown"])
    def test_unavailable_and_unknown_are_both_down(self, state):
        # `unknown` must count as down. Treating only `unavailable` as down would silently pass
        # every never-reported entity, and a long-lived instance carries many of them.
        assert entity_is_available(state) is False


class TestSummariseStates:
    def test_counts_split_by_availability(self):
        s = summarise_states(
            [
                {"entity_id": "light.kitchen", "state": "on"},
                {"entity_id": "light.hall", "state": "unavailable"},
                {"entity_id": "sensor.temp", "state": "21.0"},
            ]
        )
        assert (s["total"], s["available"], s["unavailable"]) == (3, 2, 1)

    def test_groups_by_domain(self):
        s = summarise_states(
            [
                {"entity_id": "light.a", "state": "on"},
                {"entity_id": "light.b", "state": "unknown"},
                {"entity_id": "sensor.c", "state": "5"},
            ]
        )
        assert s["by_domain"]["light"] == {"total": 2, "available": 1}
        assert s["by_domain"]["sensor"] == {"total": 1, "available": 1}

    def test_malformed_rows_are_skipped_not_fatal(self):
        # A health snapshot must stay runnable while something is broken -- that is when it is
        # actually needed.
        s = summarise_states(
            [
                {"entity_id": "light.a", "state": "on"},
                "not-a-dict",
                {"no_entity_id": True},
                None,
            ]
        )
        assert s["total"] == 1

    def test_empty_and_none_are_zero_not_an_exception(self):
        assert summarise_states([])["total"] == 0
        assert summarise_states(None)["total"] == 0


class TestSupervisorReadMap:
    """The Supervisor module's endpoint table.

    Asserted because a typo here produces a 404 that reads like "the integration is missing"
    rather than "the module asked for the wrong path".

    Parsed with `ast` rather than imported: the module imports its collection's module_utils,
    which are not on sys.path in a bare pytest run, so importing it would SKIP -- and a skipped
    test asserts nothing while looking green. Parsing tests the real source either way.
    """

    def _reads(self):
        import ast
        import os as _os

        src = _os.path.join(
            _os.path.dirname(__file__),
            "..",
            "..",
            "plugins",
            "modules",
            "homeassistant_supervisor.py",
        )
        tree = ast.parse(open(src).read())
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_READS":
                return ast.literal_eval(node.value)
        raise AssertionError("_READS not found in homeassistant_supervisor.py")

    def test_every_documented_fact_has_an_endpoint(self):
        assert set(self._reads()) == {"supervisor", "addons", "host", "os", "core", "backups"}

    def test_no_endpoint_has_a_leading_slash(self):
        # build_url strips a leading slash, so one here is harmless but misleading; a trailing
        # one would produce a double slash the Supervisor 404s on.
        for path in self._reads().values():
            assert not path.startswith("/") and not path.endswith("/")


class TestSupervisorActions:
    """The action list, checked against the module's own argument spec.

    Parsed rather than imported, for the same reason as the read map: importing pulls in
    the collection's module_utils, which are not on sys.path in a bare pytest run, and a SKIPPED
    test asserts nothing while looking green.
    """

    def _src(self):
        import os as _os

        return open(
            _os.path.join(
                _os.path.dirname(__file__),
                "..",
                "..",
                "plugins",
                "modules",
                "homeassistant_supervisor.py",
            )
        ).read()

    def test_every_lifecycle_action_is_a_valid_choice(self):
        # These are the actions the update/rollback plays call. A rename here without updating the
        # argspec fails at RUN time on a box mid-update, which is the worst moment to find out.
        src = self._src()
        for action in (
            "core_update",
            "backup_full",
            "backup_partial",
            "backup_restore",
            "backup_remove",
            "core_check",
            "core_restart",
            "core_stop",
            "core_start",
        ):
            assert '"{0}"'.format(action) in src, action

    def test_destructive_actions_require_their_target(self):
        # backup_restore and backup_remove take a slug. Without required_if, omitting it would send
        # a request to .../backups//restore/partial — a malformed URL rather than a clear error.
        src = self._src()
        assert '["action", "backup_restore", ["backup_slug"]]' in src
        assert '["action", "backup_remove", ["backup_slug"]]' in src

    def test_long_operations_get_a_long_timeout(self):
        # An update pulls an image; a backup and a restore move gigabytes. At the 30s default a
        # SUCCEEDING operation reports "could not reach the Supervisor", which invites a retry that
        # would run it twice.
        src = self._src()
        assert "_very_slow" in src
        for action in ("core_update", "backup_full", "backup_partial", "backup_restore"):
            assert action in src.split("_very_slow")[1][:200], action
