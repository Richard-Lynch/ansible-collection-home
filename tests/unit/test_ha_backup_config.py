# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for the ha_backup_config module.

The schedule decision and the websocket conversation are tested against a fake socket that answers
like Home Assistant, so every assertion runs the module's REAL code: no live appliance, no copy of
its logic. Each rule is tested both ways (the case it acts on, and the near-miss it leaves alone).

Run: python -m pytest tests/unit -q (from the collection root)
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "modules"))

import ha_backup_config as m  # noqa: E402

DAILY = {"recurrence": "daily", "time": "04:15"}


class FakeHA:
    """A websocket that answers like Home Assistant and records what it was sent."""

    def __init__(self, schedule, auth_ok=True, fail=()):
        self.schedule = dict(schedule)
        self.auth_ok = auth_ok
        self.fail = set(fail)
        self.sent = []
        self.replies = [{"type": "auth_required"}]

    def recv(self):
        return json.dumps(self.replies.pop(0))

    def send(self, raw):
        msg = json.loads(raw)
        self.sent.append(msg)
        if msg["type"] == "auth":
            self.replies.append({"type": "auth_ok" if self.auth_ok else "auth_invalid"})
            return
        if msg["type"] in self.fail:
            self.replies.append({"id": msg["id"], "success": False, "error": {"code": "x"}})
            return
        result = {}
        if msg["type"] == "backup/config/info":
            result = {"config": {"schedule": dict(self.schedule)}}
        elif msg["type"] == "backup/config/update":
            sched = msg["schedule"]
            self.schedule = {
                "recurrence": sched["recurrence"],
                "time": sched.get("time", "") + ":00",
            }
        self.replies.append({"id": msg["id"], "success": True, "result": result})

    def types(self):
        return [s["type"] for s in self.sent]


def session(ha):
    s = m.Session(ha)
    s.authenticate("tok")
    return s


class TestDecision:
    def test_matching_schedule_hh_mm_ss_is_not_an_update(self):
        assert not m.schedule_needs_update({"recurrence": "daily", "time": "04:15:00"}, DAILY)

    def test_different_time_is_an_update(self):
        assert m.schedule_needs_update({"recurrence": "daily", "time": "05:15:00"}, DAILY)

    def test_never_to_daily_with_no_time_is_an_update(self):
        assert m.schedule_needs_update({"recurrence": "never", "time": None}, DAILY)

    def test_never_ignores_time(self):
        assert not m.schedule_needs_update(
            {"recurrence": "never", "time": "01:00:00"},
            {"recurrence": "never", "time": "04:15"},
        )

    def test_empty_live_schedule_is_an_update(self):
        assert m.schedule_needs_update({}, DAILY)

    def test_never_payload_carries_no_time(self):
        assert m.update_payload({"recurrence": "never", "time": "04:15"})["schedule"] == {
            "recurrence": "never"
        }


class TestTime:
    @pytest.mark.parametrize("value", ["04:15", "00:00", "23:59"])
    def test_valid(self, value):
        assert m.TIME_RE.match(value)

    @pytest.mark.parametrize(
        "value", ["4:15", "24:00", "04:60", "04:15:00", "04:15\n", "noon", "\u0660\u0664:15"]
    )
    def test_invalid(self, value):
        assert not m.TIME_RE.match(value)


class TestWsUrl:
    @pytest.mark.parametrize(
        "base, want",
        [
            ("http://ha:8123", "ws://ha:8123/api/websocket"),
            ("https://ha.example.test/", "wss://ha.example.test/api/websocket"),
            # an `http` inside the host must not be rewritten (a blind replace would)
            ("http://http-ha:8123", "ws://http-ha:8123/api/websocket"),
        ],
    )
    def test_scheme_mapped_once(self, base, want):
        assert m.ws_url(base) == want


class TestConfigure:
    def test_converged_schedule_sends_no_update(self):
        ha = FakeHA({"recurrence": "daily", "time": "04:15:00"})
        changed, sched, triggered = m.configure(session(ha), DAILY, run_now=False, check_mode=False)
        assert (changed, triggered) == (False, False)
        assert "backup/config/update" not in ha.types()
        assert sched["time"] == "04:15:00"

    def test_drift_is_updated_and_read_back(self):
        ha = FakeHA({"recurrence": "never", "time": None})
        changed, sched, _triggered = m.configure(
            session(ha), DAILY, run_now=False, check_mode=False
        )
        assert changed
        assert ha.types().count("backup/config/update") == 1
        assert sched == {"recurrence": "daily", "time": "04:15:00"}

    def test_check_mode_writes_nothing_and_reports_the_change(self):
        ha = FakeHA({"recurrence": "never", "time": None})
        changed, _sched, triggered = m.configure(session(ha), DAILY, run_now=True, check_mode=True)
        assert changed and not triggered
        assert not {
            "backup/config/update",
            "backup/generate_with_automatic_settings",
        } & set(ha.types())

    def test_check_mode_run_now_on_converged_schedule_is_a_change(self):
        ha = FakeHA({"recurrence": "daily", "time": "04:15:00"})
        changed, _sched, triggered = m.configure(session(ha), DAILY, run_now=True, check_mode=True)
        assert changed and not triggered
        assert "backup/generate_with_automatic_settings" not in ha.types()

    def test_check_mode_converged_without_run_now_is_no_change(self):
        ha = FakeHA({"recurrence": "daily", "time": "04:15:00"})
        changed, _sched, _triggered = m.configure(
            session(ha), DAILY, run_now=False, check_mode=True
        )
        assert not changed

    def test_run_now_waits_for_the_read_back(self):
        ha = FakeHA({"recurrence": "never"})
        m.configure(session(ha), DAILY, run_now=True, check_mode=False)
        t = ha.types()
        assert t.index("backup/config/update") < t.index("backup/generate_with_automatic_settings")
        assert t[t.index("backup/config/update") + 1] == "backup/config/info"

    def test_run_now_triggers_and_is_a_change(self):
        ha = FakeHA({"recurrence": "daily", "time": "04:15:00"})
        changed, _sched, triggered = m.configure(session(ha), DAILY, run_now=True, check_mode=False)
        assert changed and triggered
        assert "backup/generate_with_automatic_settings" in ha.types()

    def test_message_ids_increase(self):
        ha = FakeHA({"recurrence": "never"})
        m.configure(session(ha), DAILY, run_now=True, check_mode=False)
        ids = [s["id"] for s in ha.sent if "id" in s]
        assert ids == sorted(set(ids))

    def test_failed_update_raises(self):
        ha = FakeHA({"recurrence": "never"}, fail={"backup/config/update"})
        with pytest.raises(m.BackupConfigError, match="backup/config/update failed"):
            m.configure(session(ha), DAILY, run_now=False, check_mode=False)

    def test_update_that_did_not_take_raises(self):
        ha = FakeHA({"recurrence": "never"})
        real_send = ha.send

        def ignore_updates(raw):
            msg = json.loads(raw)
            if msg["type"] == "backup/config/update":
                ha.sent.append(msg)
                ha.replies.append({"id": msg["id"], "success": True, "result": {}})
                return
            real_send(raw)

        ha.send = ignore_updates
        with pytest.raises(m.BackupConfigError, match="still differs"):
            m.configure(session(ha), DAILY, run_now=True, check_mode=False)
        # the backup was never triggered under a schedule that did not apply
        assert "backup/generate_with_automatic_settings" not in ha.types()


class TestAuth:
    def test_bad_token_raises(self):
        with pytest.raises(m.BackupConfigError, match="authentication failed"):
            session(FakeHA({}, auth_ok=False))

    def test_good_token_authenticates(self):
        ha = FakeHA({})
        session(ha)
        assert ha.sent == [{"type": "auth", "access_token": "tok"}]
