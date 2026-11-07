# Copyright: (c) 2026, Richard Lynch
# GNU General Public License v3.0+ (see https://www.gnu.org/licenses/gpl-3.0.txt)
# SPDX-License-Identifier: GPL-3.0-or-later
"""A minimal authenticated Home Assistant WebSocket client (module_utils; also importable by a
controller script through the collection path).

WHY THIS EXISTS. Parts of HA are reachable ONLY over the WebSocket API -- the entity registry and
the zone/helper collections among them. The REST API cannot touch either: `DELETE /api/states/<id>`
removes an in-memory state object that HA immediately recreates, and there is no REST route to a
zone's stored config at all.

Extracted so the handshake exists once. Two callers now need it, and an auth check that is subtly
different between them is exactly the kind of thing nobody notices until a call fails with a
confusing "not found" instead of a permission error.

The token is read from STDIN by callers, never argv: a command line is visible in `ps` to every
user on the box for the lifetime of the process.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def ws_url(base_url: str) -> str:
    return base_url.replace("https://", "wss://").replace("http://", "ws://") + "/api/websocket"


class HomeAssistantWS:
    """An authenticated WebSocket connection, used as a context manager.

    Every handshake step is checked. An auth failure that goes unnoticed here surfaces much later
    as a confusing "not found" rather than as the permission problem it actually is.
    """

    def __init__(self, base_url: str, token: str, timeout: int = 15) -> None:
        self._base_url = base_url
        self._token = token
        self._timeout = timeout
        self._ws: Any = None
        self._next_id = 1

    def __enter__(self) -> HomeAssistantWS:
        # websocket-client, already pinned in requirements.txt for the Kuma tooling. Deliberately
        # not the `websockets` package: a second WebSocket library to save a few lines is not
        # worth the dependency.
        import websocket

        self._ws = websocket.create_connection(ws_url(self._base_url), timeout=self._timeout)

        hello = json.loads(self._ws.recv())
        if hello.get("type") != "auth_required":
            raise RuntimeError(f"unexpected greeting {hello.get('type')!r}")

        self._ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        auth = json.loads(self._ws.recv())
        if auth.get("type") != "auth_ok":
            raise RuntimeError(f"authentication rejected ({auth.get('message', auth.get('type'))})")
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._ws is not None:
            self._ws.close()

    def command(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Send one command and return its result message.

        Messages are correlated by id rather than assuming the next frame is the answer: HA
        interleaves event frames on the same socket, and reading blindly would eventually pick one
        up and report it as a failed command.
        """
        message_id = self._next_id
        self._next_id += 1
        self._ws.send(json.dumps({**payload, "id": message_id}))

        while True:
            frame = json.loads(self._ws.recv())
            if frame.get("id") == message_id and frame.get("type") == "result":
                return frame


def token_from_stdin() -> str:
    token = sys.stdin.read().strip()
    if not token:
        sys.stderr.write("ERROR: no token on stdin\n")
        raise SystemExit(2)
    return token
