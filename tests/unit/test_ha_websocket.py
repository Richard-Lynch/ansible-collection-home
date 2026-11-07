# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""ha_websocket against a scripted fake `websocket` module: the handshake is checked step by step,
an auth rejection is a permission error (not a later "not found"), commands are correlated by id
through interleaved event frames, and the token is read from stdin, never argv."""

from __future__ import annotations

import io
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "plugins", "module_utils"))

import ha_websocket as hw  # noqa: E402


class FakeSocket:
    def __init__(self, frames):
        self.frames = [json.dumps(f) for f in frames]
        self.sent = []
        self.closed = False

    def recv(self):
        return self.frames.pop(0)

    def send(self, text):
        self.sent.append(json.loads(text))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_ws(monkeypatch):
    holder = {}

    def create_connection(url, timeout):
        holder["url"] = url
        holder["timeout"] = timeout
        return holder["sock"]

    mod = types.ModuleType("websocket")
    mod.create_connection = create_connection
    monkeypatch.setitem(sys.modules, "websocket", mod)

    def arm(frames):
        holder["sock"] = FakeSocket(frames)
        return holder

    return arm


def test_ws_url_maps_schemes():
    assert hw.ws_url("https://ha.example.test") == "wss://ha.example.test/api/websocket"
    assert hw.ws_url("http://127.0.0.1:8123") == "ws://127.0.0.1:8123/api/websocket"


def test_handshake_and_id_correlation(fake_ws):
    h = fake_ws(
        [
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {"type": "event", "id": 7},  # an interleaved event frame, not the answer
            {"type": "result", "id": 1, "success": True, "result": {"ok": 1}},
        ]
    )
    with hw.HomeAssistantWS("http://127.0.0.1:8123", "tok-fixture", timeout=3) as ws:
        out = ws.command({"type": "config/zone/list"})
    assert out["result"] == {"ok": 1}
    assert h["url"] == "ws://127.0.0.1:8123/api/websocket" and h["timeout"] == 3
    assert h["sock"].sent[0] == {"type": "auth", "access_token": "tok-fixture"}
    assert h["sock"].sent[1]["id"] == 1 and h["sock"].closed


def test_auth_rejection_is_a_permission_error(fake_ws):
    fake_ws(
        [{"type": "auth_required"}, {"type": "auth_invalid", "message": "Invalid access token"}]
    )
    with pytest.raises(RuntimeError, match="authentication rejected"):
        with hw.HomeAssistantWS("http://127.0.0.1:8123", "tok-bad"):
            pass


def test_unexpected_greeting_is_an_error(fake_ws):
    fake_ws([{"type": "hello"}])
    with pytest.raises(RuntimeError, match="unexpected greeting"):
        with hw.HomeAssistantWS("http://127.0.0.1:8123", "tok"):
            pass


def test_token_from_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("  tok-from-stdin\n"))
    assert hw.token_from_stdin() == "tok-from-stdin"
