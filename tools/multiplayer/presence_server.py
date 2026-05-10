from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import websockets
from websockets.asyncio.server import ServerConnection


ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger("solamon.presence")
ALLOWED_UPDATE_TYPES = {
    "PUSH_SELF",
    "CLIENT_MAP_UPDATE",
    "CLIENT_MOVE_START",
    "CLIENT_MOVE_COMPLETE",
    "CLIENT_FACING",
    "CLIENT_CHAT",
}


class PresenceServer:
    def __init__(self) -> None:
        self.sockets: dict[str, ServerConnection] = {}
        self.presence: dict[str, dict[str, Any]] = {}

    async def handler(self, websocket: ServerConnection) -> None:
        cuuid = str(uuid4())
        try:
            first = await asyncio.wait_for(websocket.recv(), timeout=8.0)
            event = self._decode_event(first)
            if event.get("type") != "PUSH_SELF":
                await websocket.close(code=4401, reason="first event must be PUSH_SELF")
                return

            self.sockets[cuuid] = websocket
            self.presence[cuuid] = self._presence_from_event(cuuid, event)
            LOGGER.info(
                "accepted %s owner=%s character=%s map=%s tile=%s",
                cuuid,
                self.presence[cuuid]["owner"],
                self.presence[cuuid]["character_mint"],
                self.presence[cuuid]["map_name"],
                self.presence[cuuid]["char_dict"].get("tile_pos"),
            )
            await self._send_existing_players(cuuid)
            await self._broadcast(cuuid, self._event_for_client(cuuid, "PUSH_SELF"))

            async for raw in websocket:
                event = self._decode_event(raw)
                if event.get("type") not in ALLOWED_UPDATE_TYPES:
                    continue
                if not self._identity_matches(cuuid, event):
                    LOGGER.warning("Ignoring spoofed update from %s", cuuid)
                    continue
                self.presence[cuuid].update(self._presence_from_event(cuuid, event))
                LOGGER.info(
                    "update %s type=%s map=%s tile=%s",
                    cuuid,
                    event.get("type"),
                    self.presence[cuuid]["map_name"],
                    self.presence[cuuid]["char_dict"].get("tile_pos"),
                )
                await self._broadcast(
                    cuuid,
                    self._event_for_client(cuuid, str(event.get("type"))),
                )
        except Exception:
            LOGGER.exception("Presence client failed")
        finally:
            self.sockets.pop(cuuid, None)
            if cuuid in self.presence:
                self.presence.pop(cuuid, None)
                await self._broadcast(
                    cuuid,
                    {
                        "type": "CLIENT_DISCONNECTED",
                        "event_number": int(time.time() * 1000),
                        "cuuid": cuuid,
                    },
                )

    def _decode_event(self, raw: str | bytes) -> dict[str, Any]:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("event must be a JSON object")
        return data

    def _identity_matches(self, cuuid: str, event: dict[str, Any]) -> bool:
        entry = self.presence.get(cuuid)
        return bool(
            entry
            and event.get("owner") == entry.get("owner")
            and event.get("character_mint") == entry.get("character_mint")
        )

    def _presence_from_event(self, cuuid: str, event: dict[str, Any]) -> dict[str, Any]:
        char = sanitize_char(event.get("char_dict"))
        presence = {
            "cuuid": cuuid,
            "owner": event.get("owner"),
            "character_mint": event.get("character_mint"),
            "map_name": str(event.get("map_name") or ""),
            "char_dict": char,
            "updated_at": time.time(),
        }
        if event.get("type") == "CLIENT_CHAT":
            presence["message"] = sanitize_message(event.get("message"))
        return presence

    def _event_for_client(self, cuuid: str, event_type: str) -> dict[str, Any]:
        entry = self.presence[cuuid]
        return {
            "type": event_type,
            "event_number": int(entry["updated_at"] * 1000),
            "cuuid": cuuid,
            "owner": entry["owner"],
            "character_mint": entry["character_mint"],
            "map_name": entry["map_name"],
            "char_dict": entry["char_dict"],
            "message": entry.get("message"),
        }

    async def _send_existing_players(self, cuuid: str) -> None:
        websocket = self.sockets[cuuid]
        for other in self.presence:
            if other != cuuid:
                LOGGER.info("send existing %s to %s", other, cuuid)
                await websocket.send(json.dumps(self._event_for_client(other, "PUSH_SELF")))

    async def _broadcast(self, exclude: str, event: dict[str, Any]) -> None:
        message = json.dumps(event)
        sent = 0
        for cuuid, websocket in list(self.sockets.items()):
            if cuuid == exclude:
                continue
            try:
                await websocket.send(message)
                sent += 1
            except Exception:
                LOGGER.warning("Failed to broadcast to %s", cuuid)
        LOGGER.info("broadcast type=%s from=%s to=%s clients", event.get("type"), exclude, sent)


def sanitize_char(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    tile = data.get("tile_pos") or (0, 0)
    if not isinstance(tile, (list, tuple)):
        tile = (0, 0)
    x = int(tile[0]) if len(tile) >= 1 else 0
    y = int(tile[1]) if len(tile) >= 2 else 0
    facing = str(data.get("facing") or "down").lower()
    if facing not in {"up", "down", "left", "right"}:
        facing = "down"
    skin = str(data.get("skin") or "adventurer")[:48]
    skin = "".join(
        char for char in skin if char.isalnum() or char in {"_", "-"}
    )
    if not skin:
        skin = "adventurer"
    return {
        "tile_pos": (max(0, x), max(0, y)),
        "name": str(data.get("name") or "")[:32],
        "facing": facing,
        "running": bool(data.get("running", False)),
        "skin": skin,
    }


def sanitize_message(raw: Any) -> str:
    message = str(raw or "")
    message = "".join(ch for ch in message if ch.isprintable() and ch not in "\r\n\t")
    return message.strip()[:120]


async def main() -> None:
    parser = argparse.ArgumentParser(description="Solamon live presence server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=40081)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = PresenceServer()
    async with websockets.serve(server.handler, args.host, args.port):
        LOGGER.info("Solamon presence server listening on %s:%s", args.host, args.port)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
