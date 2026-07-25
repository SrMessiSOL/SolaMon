from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.datastructures import Headers
from websockets.http11 import Request

from tools.multiplayer.presence_server import (
    PRESENCE_AUTH_DOMAIN,
    PresenceServer,
    health_response,
    verify_presence_event,
)


def _b58encode(raw: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    number = int.from_bytes(raw, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = alphabet[remainder] + encoded
    padding = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * padding + (encoded or "1")


def _signed_event(
    private_key: Ed25519PrivateKey,
    *,
    timestamp: int = 1_700_000_000_000,
    nonce: str = "unique-presence-nonce",
) -> dict:
    owner = _b58encode(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    character_mint = "11111111111111111111111111111111"
    request = {
        "version": 1,
        "owner": owner,
        "characterMint": character_mint,
        "timestamp": timestamp,
        "nonce": nonce,
    }
    message = PRESENCE_AUTH_DOMAIN + json.dumps(
        request,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return {
        "type": "PUSH_SELF",
        "owner": owner,
        "character_mint": character_mint,
        "presence_auth": {
            "request": request,
            "signature": base64.b64encode(private_key.sign(message)).decode("ascii"),
        },
    }


def test_presence_auth_accepts_owner_signature_and_blocks_replay() -> None:
    event = _signed_event(Ed25519PrivateKey.generate())
    used_nonces: dict[str, float] = {}
    assert verify_presence_event(
        event,
        used_nonces,
        now_ms=1_700_000_000_000,
        chain_verifier=lambda _owner, _character: True,
    )
    assert not verify_presence_event(
        event,
        used_nonces,
        now_ms=1_700_000_000_000,
        chain_verifier=lambda _owner, _character: True,
    )


def test_presence_auth_rejects_tampering_and_stale_requests() -> None:
    event = _signed_event(Ed25519PrivateKey.generate())
    event["character_mint"] = "tampered"
    assert not verify_presence_event(
        event,
        {},
        now_ms=1_700_000_000_000,
        chain_verifier=lambda _owner, _character: True,
    )
    stale = _signed_event(Ed25519PrivateKey.generate(), timestamp=1_699_999_000_000)
    assert not verify_presence_event(
        stale,
        {},
        now_ms=1_700_000_000_000,
        chain_verifier=lambda _owner, _character: True,
    )


def test_presence_auth_requires_initialized_onchain_character() -> None:
    event = _signed_event(Ed25519PrivateKey.generate())
    assert not verify_presence_event(
        event,
        {},
        now_ms=1_700_000_000_000,
        chain_verifier=lambda _owner, _character: False,
    )


def test_health_endpoint_is_http_only() -> None:
    response = health_response(object(), Request("/health", Headers()))
    assert response is not None
    assert response.status_code == 200
    assert json.loads(response.body)["ok"] is True
    websocket_headers = Headers([("Upgrade", "websocket")])
    assert health_response(object(), Request("/", websocket_headers)) is None


class _FakeConnection:
    def __init__(self) -> None:
        self.closed: list[tuple[int, str]] = []
        self.messages: list[dict[str, Any]] = []

    async def close(self, *, code: int, reason: str) -> None:
        self.closed.append((code, reason))

    async def send(self, raw: str) -> None:
        self.messages.append(json.loads(raw))


def _presence_event(owner: str, character_mint: str) -> dict[str, Any]:
    return {
        "type": "PUSH_SELF",
        "owner": owner,
        "character_mint": character_mint,
        "map_name": "spyder_paper_town.tmx",
        "char_dict": {
            "tile_pos": [10, 7],
            "name": "Duplicate Player",
            "facing": "down",
            "skin": "adventurer",
        },
    }


def test_duplicate_player_identity_is_rejected() -> None:
    async def scenario() -> None:
        server = PresenceServer()
        observer = _FakeConnection()
        first = _FakeConnection()
        second = _FakeConnection()
        observer_event = _presence_event(
            "observer-owner",
            "observer-character",
        )
        duplicate_event = _presence_event("same-owner", "same-character")

        assert await server._register_presence(
            "observer",
            observer,  # type: ignore[arg-type]
            observer_event,
        ) == "accepted"
        assert await server._register_presence(
            "first",
            first,  # type: ignore[arg-type]
            duplicate_event,
        ) == "accepted"
        assert await server._register_presence(
            "second",
            second,  # type: ignore[arg-type]
            duplicate_event,
        ) == "duplicate"

        identity = ("same-owner", "same-character")
        assert not first.closed
        assert server.sockets["first"] is first
        assert "second" not in server.sockets
        assert "second" not in server.presence
        assert server.identity_clients[identity] == "first"
        assert not observer.messages

    asyncio.run(scenario())


def test_same_wallet_can_use_distinct_character_identities() -> None:
    async def scenario() -> None:
        server = PresenceServer()
        first = _FakeConnection()
        second = _FakeConnection()

        assert await server._register_presence(
            "first",
            first,  # type: ignore[arg-type]
            _presence_event("same-owner", "character-one"),
        ) == "accepted"
        assert await server._register_presence(
            "second",
            second,  # type: ignore[arg-type]
            _presence_event("same-owner", "character-two"),
        ) == "accepted"

        assert not first.closed
        assert set(server.sockets) == {"first", "second"}

    asyncio.run(scenario())
