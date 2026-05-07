# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tuxemon.constants import paths
from tuxemon.save_system.save_state import SaveData
from tuxemon.user_config import CONFIG

from tuxemon.chain.character import CharacterProfile
from tuxemon.chain.save_blob import hash_payload, canonical_save_payload

logger = logging.getLogger(__name__)

CHAIN_PROJECTION_DIR = paths.USER_GAME_SAVE_DIR / "chain"
CHAIN_SCHEMA = "solamon-chain-projection-v1"
STATE_ONLY_ITEM_SLUGS = frozenset(
    {
        "friendship_scroll",
        "nu_phone",
        "app_banking",
        "app_map",
        "app_tuxepedia",
    }
)


@dataclass(frozen=True)
class CurrencyProjection:
    mint: str
    symbol: str
    decimals: int
    wallet_amount: int
    bank_amount: int

    @property
    def total_amount(self) -> int:
        return self.wallet_amount + self.bank_amount


@dataclass(frozen=True)
class NftProjection:
    kind: str
    collection_mint: str | None
    game_id: str
    instance_id: str
    amount: int
    state_hash: str
    metadata: Mapping[str, Any]


def _stable_hash(data: Mapping[str, Any]) -> str:
    return hash_payload(dict(data))


def _iter_box_assets(
    boxes: Mapping[str, Sequence[Mapping[str, Any]]],
) -> Iterable[tuple[str, Mapping[str, Any]]]:
    for box_id, assets in boxes.items():
        for asset in assets:
            yield box_id, asset


def _project_items(npc_state: Any) -> list[NftProjection]:
    items: list[NftProjection] = []

    def append_item(location: str, item: Mapping[str, Any]) -> None:
        slug = str(item.get("slug", "unknown"))
        if slug in STATE_ONLY_ITEM_SLUGS:
            logger.info("Keeping Solamon item %s as on-chain state only.", slug)
            return
        instance_id = str(item.get("instance_id", slug))
        quantity = int(item.get("quantity", 1))
        payload = {
            "kind": "item",
            "slug": slug,
            "instance_id": instance_id,
            "quantity": quantity,
            "wear": item.get("wear", 0),
            "location": location,
        }
        items.append(
            NftProjection(
                kind="item",
                collection_mint=CONFIG.item_nft_collection_mint,
                game_id=slug,
                instance_id=instance_id,
                amount=max(quantity, 1),
                state_hash=_stable_hash(payload),
                metadata=payload,
            )
        )

    for item in npc_state.items or []:
        append_item("bag", item)

    for box_id, item in _iter_box_assets(npc_state.item_boxes or {}):
        append_item(f"locker:{box_id}", item)

    return items


def _project_monsters(npc_state: Any) -> list[NftProjection]:
    monsters: list[NftProjection] = []

    def append_monster(location: str, monster: Mapping[str, Any]) -> None:
        slug = str(monster.get("slug", "unknown"))
        instance_id = str(monster.get("instance_id", slug))
        payload = {
            "kind": "monster",
            "slug": slug,
            "instance_id": instance_id,
            "level": monster.get("level"),
            "total_experience": monster.get("total_experience"),
            "current_hp": monster.get("current_hp"),
            "status": monster.get("status", {}),
            "moves": monster.get("moves", []),
            "location": location,
        }
        monsters.append(
            NftProjection(
                kind="monster",
                collection_mint=CONFIG.monster_nft_collection_mint,
                game_id=slug,
                instance_id=instance_id,
                amount=1,
                state_hash=_stable_hash(payload),
                metadata=payload,
            )
        )

    for monster in npc_state.monsters or []:
        append_monster("party", monster)

    for box_id, monster in _iter_box_assets(npc_state.monster_boxes or {}):
        append_monster(f"kennel:{box_id}", monster)

    return monsters


def _project_badges(save_data: SaveData) -> list[NftProjection]:
    badges: list[NftProjection] = []
    world_state = save_data.world_state
    if not world_state:
        return badges

    factions = world_state.factions_manager or {}
    for faction_slug, faction_data in factions.items():
        if not isinstance(faction_data, Mapping):
            continue
        badge_id = faction_data.get("badge_id")
        if not badge_id:
            continue
        payload = {
            "kind": "badge",
            "badge_id": badge_id,
            "faction": faction_slug,
        }
        badges.append(
            NftProjection(
                kind="badge",
                collection_mint=CONFIG.badge_nft_collection_mint,
                game_id=str(badge_id),
                instance_id=f"{faction_slug}:{badge_id}",
                amount=1,
                state_hash=_stable_hash(payload),
                metadata=payload,
            )
        )

    return badges


def _canonical_save_hash(save_data: SaveData) -> str:
    return hash_payload(canonical_save_payload(save_data))


def build_chain_projection(
    save_data: SaveData,
    character: CharacterProfile | None = None,
) -> dict[str, Any]:
    if CONFIG.spl_currency_mint is None:
        raise ValueError("game.spl_currency_mint must be configured")
    if save_data.npc_state is None:
        raise ValueError("Cannot project chain assets without player state")

    money = save_data.npc_state.money or {}
    currency = CurrencyProjection(
        mint=CONFIG.spl_currency_mint,
        symbol=CONFIG.spl_currency_symbol,
        decimals=CONFIG.spl_currency_decimals,
        wallet_amount=int(money.get("money", 0)),
        bank_amount=int(money.get("bank_account", 0)),
    )
    nfts = [
        *_project_items(save_data.npc_state),
        *_project_monsters(save_data.npc_state),
        *_project_badges(save_data),
    ]
    save_hash = _canonical_save_hash(save_data)
    tile_x = 0
    tile_y = 0
    if save_data.npc_state.tile_pos:
        tile_x, tile_y = save_data.npc_state.tile_pos
    body = {
        "schema": CHAIN_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "rpc_url": CONFIG.solana_rpc_url,
        "wallet_path": CONFIG.solana_wallet_path,
        "save_hash": save_hash,
        "location": {
            "map_id": save_data.npc_state.current_map or "",
            "tile_x": max(0, min(int(tile_x), 65535)),
            "tile_y": max(0, min(int(tile_y), 65535)),
        },
        "currency": {
            **asdict(currency),
            "total_amount": currency.total_amount,
            "treasury_owner": CONFIG.spl_treasury_owner,
            "treasury_token_account": CONFIG.spl_treasury_token_account,
        },
        "nfts": [asdict(asset) for asset in nfts],
    }
    if character:
        body["character"] = {
            "owner": character.owner,
            "name": character.name,
            "avatar_slug": character.avatar_slug,
            "skin": character.avatar_slug,
            "character_mint": character.character_mint,
            "character_collection_mint": character.character_collection_mint,
            "character_mint_status": character.character_mint_status,
            "creation_payload_hash": character.creation_payload_hash,
            "latest_save_hash": save_hash,
        }
    body["projection_hash"] = _stable_hash(body)
    return body


def write_chain_projection(
    save_data: SaveData,
    save_path: Path,
    character: CharacterProfile | None = None,
    save_blob_uri: str | None = None,
) -> tuple[Path, str]:
    CHAIN_PROJECTION_DIR.mkdir(parents=True, exist_ok=True)
    projection_path = CHAIN_PROJECTION_DIR / f"{save_path.stem}.chain.json"
    projection = build_chain_projection(save_data, character)
    if save_blob_uri:
        projection["save_blob_uri"] = save_blob_uri
        projection["projection_hash"] = _stable_hash(projection)

    with projection_path.open("w", encoding="utf-8") as projection_file:
        json.dump(projection, projection_file, indent=2, sort_keys=True)

    logger.info("Wrote Solamon chain projection: %s", projection_path)
    return projection_path, str(projection["save_hash"])
