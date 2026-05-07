# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tuxemon.chain.base58 import b58encode
from tuxemon.chain.fees import MIN_CHAIN_ACTION_LAMPORTS
from tuxemon.chain.rpc import SolanaRpcClient
from tuxemon.chain.wallet import UnlockedWallet
from tuxemon.constants import paths
from tuxemon.user_config import CONFIG

CHARACTER_SCHEMA = "solamon-character-v1"
CHARACTER_DIR = paths.USER_GAME_SAVE_DIR / "characters"


@dataclass(frozen=True)
class CharacterProfile:
    schema: str
    owner: str
    name: str
    avatar_slug: str
    character_mint: str
    created_at: str
    creation_payload_hash: str
    creation_signature: str
    character_collection_mint: str | None = None
    character_mint_status: str = "pending"
    latest_save_hash: str | None = None
    latest_save_uri: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CharacterProfile:
        return cls(
            schema=str(data["schema"]),
            owner=str(data["owner"]),
            name=str(data["name"]),
            avatar_slug=str(data["avatar_slug"]),
            character_mint=str(data["character_mint"]),
            created_at=str(data["created_at"]),
            creation_payload_hash=str(data["creation_payload_hash"]),
            creation_signature=str(data["creation_signature"]),
            character_collection_mint=data.get("character_collection_mint"),
            character_mint_status=str(
                data.get("character_mint_status", "pending")
            ),
            latest_save_hash=data.get("latest_save_hash"),
            latest_save_uri=data.get("latest_save_uri"),
        )


def character_profile_path(owner: str) -> Path:
    safe_owner = "".join(ch for ch in owner if ch.isalnum())
    return CHARACTER_DIR / f"{safe_owner}.json"


def load_character_profile(owner: str) -> CharacterProfile | None:
    path = character_profile_path(owner)
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as profile_file:
        return CharacterProfile.from_dict(json.load(profile_file))


def discover_character_profile(owner: str) -> CharacterProfile | None:
    collection = CONFIG.character_nft_collection_mint
    if not collection:
        return None

    result = subprocess.run(
        ["node", "find-character.mjs", owner],
        cwd=_solana_tools_dir(),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    data = json.loads(result.stdout)
    if data is None:
        return None

    profile = CharacterProfile(
        schema=CHARACTER_SCHEMA,
        owner=owner,
        name=data.get("name") or "Player",
        avatar_slug="player",
        character_mint=data["mint"],
        created_at=datetime.now(UTC).isoformat(),
        creation_payload_hash="discovered-on-devnet",
        creation_signature="",
        character_collection_mint=data.get("collection") or collection,
        character_mint_status="minted-devnet",
    )
    save_character_profile(profile)
    return profile


def save_character_profile(profile: CharacterProfile) -> None:
    CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
    with character_profile_path(profile.owner).open(
        "w", encoding="utf-8"
    ) as profile_file:
        json.dump(profile.to_dict(), profile_file, indent=2, sort_keys=True)


def _solana_tools_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "tools" / "solana"


def mint_character_nft_devnet(
    profile: CharacterProfile,
    wallet: UnlockedWallet | None = None,
) -> CharacterProfile:
    env = os.environ.copy()
    if wallet is not None:
        balance = SolanaRpcClient().get_sol_balance(wallet.public_key)
        if balance < MIN_CHAIN_ACTION_LAMPORTS:
            raise RuntimeError(
                "Not enough SOL in the player vault to create the Solamon NFT. "
                f"Deposit at least {MIN_CHAIN_ACTION_LAMPORTS / 1_000_000_000:.3f} "
                f"SOL to {wallet.public_key} and try again."
            )
        env["SOLAMON_SECRET_KEY_BASE64"] = base64.b64encode(
            wallet.solana_secret_key()
        ).decode("ascii")
    _require_remote_authority_for_distribution(env)
    profile_path = character_profile_path(profile.owner)
    result = subprocess.run(
        ["node", "mint-character.mjs", profile_path.resolve().as_posix()],
        cwd=_solana_tools_dir(),
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())

    minted = load_character_profile(profile.owner)
    if minted is None:
        raise RuntimeError("Character NFT mint completed but profile is missing")
    if wallet is not None:
        create_env = {
            **os.environ,
            "SOLAMON_SECRET_KEY_BASE64": base64.b64encode(
                wallet.solana_secret_key()
            ).decode("ascii"),
        }
        _require_remote_authority_for_distribution(create_env)
        result = subprocess.run(
            [
                "node",
                "create-player-account.mjs",
                profile_path.resolve().as_posix(),
            ],
            cwd=_solana_tools_dir(),
            check=False,
            capture_output=True,
            env=create_env,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return minted


def _require_remote_authority_for_distribution(env: dict[str, str]) -> None:
    if env.get("SOLAMON_REQUIRE_AUTHORITY") == "1" and not env.get(
        "SOLAMON_AUTHORITY_URL"
    ):
        raise RuntimeError(
            "SOLAMON_AUTHORITY_URL is required when SOLAMON_REQUIRE_AUTHORITY=1"
        )


def _hash_payload(payload: dict[str, Any]) -> bytes:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def create_character_profile(
    wallet: UnlockedWallet,
    name: str,
    avatar_slug: str = "player",
) -> CharacterProfile:
    if CONFIG.character_nft_collection_mint:
        balance = SolanaRpcClient().get_sol_balance(wallet.public_key)
        if balance < MIN_CHAIN_ACTION_LAMPORTS:
            raise RuntimeError(
                "Not enough SOL in the player vault to create the Solamon NFT. "
                f"Deposit at least {MIN_CHAIN_ACTION_LAMPORTS / 1_000_000_000:.3f} "
                f"SOL to {wallet.public_key} and try again."
            )

    owner = wallet.public_key
    created_at = datetime.now(UTC).isoformat()
    payload = {
        "schema": CHARACTER_SCHEMA,
        "owner": owner,
        "name": name,
        "avatar_slug": avatar_slug,
        "created_at": created_at,
    }
    payload_hash = _hash_payload(payload)
    signature = wallet.sign_message(payload_hash)
    character_mint = b58encode(
        hashlib.sha256(b"character-nft" + payload_hash).digest()
    )

    profile = CharacterProfile(
        schema=CHARACTER_SCHEMA,
        owner=owner,
        name=name,
        avatar_slug=avatar_slug,
        character_mint=character_mint,
        created_at=created_at,
        creation_payload_hash=payload_hash.hex(),
        creation_signature=base64.b64encode(signature).decode("ascii"),
        character_collection_mint=CONFIG.character_nft_collection_mint,
    )
    save_character_profile(profile)
    if CONFIG.character_nft_collection_mint:
        return mint_character_nft_devnet(profile, wallet)
    return profile


def update_character_save_anchor(
    profile: CharacterProfile,
    save_hash: str,
    save_uri: str,
) -> CharacterProfile:
    updated = CharacterProfile(
        schema=profile.schema,
        owner=profile.owner,
        name=profile.name,
        avatar_slug=profile.avatar_slug,
        character_mint=profile.character_mint,
        created_at=profile.created_at,
        creation_payload_hash=profile.creation_payload_hash,
        creation_signature=profile.creation_signature,
        character_collection_mint=profile.character_collection_mint,
        character_mint_status=profile.character_mint_status,
        latest_save_hash=save_hash,
        latest_save_uri=save_uri,
    )
    save_character_profile(updated)
    return updated
