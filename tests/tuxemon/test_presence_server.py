from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from websockets.datastructures import Headers
from websockets.http11 import Request

from tools.multiplayer.presence_server import (
    PRESENCE_AUTH_DOMAIN,
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
