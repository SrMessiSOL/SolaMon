from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import subprocess
import time
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

import websockets
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from websockets.asyncio.server import ServerConnection
from websockets.datastructures import Headers
from websockets.http11 import Request, Response


ROOT = Path(__file__).resolve().parents[2]
SOLANA_TOOLS = ROOT / "tools" / "solana"
LOGGER = logging.getLogger("solamon.presence")
PRESENCE_AUTH_DOMAIN = b"solamon-presence-v1:"
MAX_CLIENTS = 32
MAX_EVENTS_PER_WINDOW = 80
RATE_WINDOW_SECONDS = 5.0
ALLOWED_UPDATE_TYPES = {
    "PUSH_SELF",
    "CLIENT_MAP_UPDATE",
    "CLIENT_MOVE_START",
    "CLIENT_MOVE_COMPLETE",
    "CLIENT_FACING",
    "CLIENT_CHAT",
    "PING",
}


class PresenceServer:
    def __init__(self) -> None:
        self.sockets: dict[str, ServerConnection] = {}
        self.presence: dict[str, dict[str, Any]] = {}
        self.identity_clients: dict[tuple[str, str], str] = {}
        self.used_nonces: dict[str, float] = {}
        self._registration_lock = asyncio.Lock()

    async def handler(self, websocket: ServerConnection) -> None:
        cuuid = str(uuid4())
        try:
            event = await self._wait_for_initial_presence(websocket)
            if not verify_presence_event(
                event,
                self.used_nonces,
            ):
                await websocket.close(code=4403, reason="invalid player identity")
                return

            registration = await self._register_presence(
                cuuid,
                websocket,
                event,
            )
            if registration == "duplicate":
                await websocket.close(
                    code=4409,
                    reason="player already connected",
                )
                return
            if registration == "full":
                await websocket.close(code=4429, reason="presence server full")
                return
            event_times: deque[float] = deque()
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
                now = time.monotonic()
                event_times.append(now)
                while event_times and now - event_times[0] > RATE_WINDOW_SECONDS:
                    event_times.popleft()
                if len(event_times) > MAX_EVENTS_PER_WINDOW:
                    await websocket.close(code=4429, reason="presence rate limit")
                    break
                event = self._decode_event(raw)
                if event.get("type") not in ALLOWED_UPDATE_TYPES:
                    continue
                if event.get("type") == "PING":
                    if cuuid in self.presence:
                        self.presence[cuuid]["updated_at"] = time.time()
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
            await self._remove_client(cuuid)

    async def _register_presence(
        self,
        cuuid: str,
        websocket: ServerConnection,
        event: dict[str, Any],
    ) -> str:
        identity = self._identity_from_event(event)
        async with self._registration_lock:
            existing = self.identity_clients.get(identity)
            if existing is not None and existing != cuuid:
                LOGGER.info(
                    "rejecting duplicate player connection existing=%s new=%s "
                    "owner=%s character=%s",
                    existing,
                    cuuid,
                    identity[0],
                    identity[1],
                )
                return "duplicate"
            if len(self.sockets) >= MAX_CLIENTS:
                return "full"

            self.sockets[cuuid] = websocket
            self.presence[cuuid] = self._presence_from_event(cuuid, event)
            self.identity_clients[identity] = cuuid
            return "accepted"

    @staticmethod
    def _identity_from_event(event: dict[str, Any]) -> tuple[str, str]:
        return (
            str(event.get("owner") or ""),
            str(event.get("character_mint") or ""),
        )

    async def _wait_for_initial_presence(
        self,
        websocket: ServerConnection,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + 45.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await websocket.close(code=4401, reason="initial presence timed out")
                raise TimeoutError("initial presence timed out")
            raw = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            event = self._decode_event(raw)
            event_type = event.get("type")
            if event_type == "PUSH_SELF":
                return event
            if event_type == "PING":
                continue
            await websocket.close(code=4401, reason="first presence event must be PUSH_SELF")
            raise ValueError(f"first presence event was {event_type!r}")

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
        failed: list[str] = []
        for cuuid, websocket in list(self.sockets.items()):
            if cuuid == exclude:
                continue
            try:
                await websocket.send(message)
                sent += 1
            except Exception:
                LOGGER.warning("Failed to broadcast to %s", cuuid)
                failed.append(cuuid)
        for cuuid in failed:
            await self._drop_stale_client(cuuid)
        LOGGER.info("broadcast type=%s from=%s to=%s clients", event.get("type"), exclude, sent)

    async def _drop_stale_client(self, cuuid: str) -> None:
        await self._remove_client(
            cuuid,
            close_code=1011,
            reason="stale presence socket",
        )

    async def _remove_client(
        self,
        cuuid: str,
        *,
        close_code: int | None = None,
        reason: str = "",
    ) -> None:
        websocket = self.sockets.pop(cuuid, None)
        entry = self.presence.pop(cuuid, None)
        if entry is not None:
            identity = self._identity_from_event(entry)
            if self.identity_clients.get(identity) == cuuid:
                self.identity_clients.pop(identity, None)
        if websocket is not None and close_code is not None:
            try:
                await websocket.close(code=close_code, reason=reason)
            except Exception:
                pass
        if entry is not None:
            await self._broadcast(
                cuuid,
                {
                    "type": "CLIENT_DISCONNECTED",
                    "event_number": int(time.time() * 1000),
                    "cuuid": cuuid,
                },
            )


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


def verify_presence_event(
    event: dict[str, Any],
    used_nonces: dict[str, float],
    *,
    now_ms: int | None = None,
    chain_verifier: Any = None,
) -> bool:
    owner = str(event.get("owner") or "")
    character_mint = str(event.get("character_mint") or "")
    envelope = event.get("presence_auth")
    if not owner or not character_mint or not isinstance(envelope, dict):
        return False
    request = envelope.get("request")
    signature_text = envelope.get("signature")
    if not isinstance(request, dict) or not isinstance(signature_text, str):
        return False
    if (
        request.get("version") != 1
        or request.get("owner") != owner
        or request.get("characterMint") != character_mint
    ):
        return False
    try:
        timestamp = int(request["timestamp"])
        nonce = str(request["nonce"])
    except (KeyError, TypeError, ValueError):
        return False
    current_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if abs(current_ms - timestamp) > 120_000 or not nonce or len(nonce) > 128:
        return False
    _prune_nonces(used_nonces, current_ms / 1000)
    if nonce in used_nonces:
        return False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(_b58decode(owner))
        signature = base64.b64decode(signature_text, validate=True)
        message = PRESENCE_AUTH_DOMAIN + json.dumps(
            request,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        public_key.verify(signature, message)
    except (InvalidSignature, ValueError, TypeError):
        return False
    verifier = verify_chain_player if chain_verifier is None else chain_verifier
    if not verifier(owner, character_mint):
        return False
    used_nonces[nonce] = current_ms / 1000
    return True


def verify_chain_player(owner: str, character_mint: str) -> bool:
    try:
        result = subprocess.run(
            ["node", "read-player-state.mjs", owner, character_mint],
            cwd=SOLANA_TOOLS,
            check=False,
            capture_output=True,
            text=True,
            timeout=12,
        )
    except Exception:
        LOGGER.exception("Unable to verify presence player on chain")
        return False
    if result.returncode != 0:
        LOGGER.warning("Presence chain verification failed: %s", result.stderr)
        return False
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError:
        return False
    return bool(
        state
        and state.get("initialized")
        and state.get("owner") == owner
        and state.get("characterMint") == character_mint
    )


def _prune_nonces(used_nonces: dict[str, float], now_seconds: float) -> None:
    for nonce, seen_at in list(used_nonces.items()):
        if now_seconds - seen_at > 300:
            used_nonces.pop(nonce, None)


def _b58decode(value: str) -> bytes:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = 0
    for char in value:
        number = number * 58 + alphabet.index(char)
    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big")
    padding = len(value) - len(value.lstrip("1"))
    result = b"\x00" * padding + decoded
    if len(result) != 32:
        raise ValueError("invalid Solana public key")
    return result


def health_response(_connection: ServerConnection, request: Request) -> Response | None:
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return None
    if request.path not in {"/", "/health"}:
        return Response(404, "Not Found", Headers(), b"not found")
    body = json.dumps(
        {"ok": True, "service": "solamon-presence", "clients": 0}
    ).encode("utf-8")
    headers = Headers(
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
        ]
    )
    return Response(200, "OK", headers, body)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Solamon live presence server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=40081)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    server = PresenceServer()
    async with websockets.serve(
        server.handler,
        args.host,
        args.port,
        process_request=health_response,
        ping_interval=20,
        ping_timeout=20,
        max_size=64 * 1024,
        max_queue=32,
    ):
        LOGGER.info("Solamon presence server listening on %s:%s", args.host, args.port)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
