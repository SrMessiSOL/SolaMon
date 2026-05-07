# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from tuxemon.chain.compact import apply_compact_payload, parse_compact_uri
from tuxemon.chain.character import CharacterProfile
from tuxemon.save_system.save import get_save_path
from tuxemon.save_system.save_state import SaveData

logger = logging.getLogger(__name__)


def projection_path_for_slot(slot: int) -> Path:
    save_path = get_save_path(slot)
    return save_path.parent / "chain" / f"{save_path.stem}.chain.json"


def load_slot_projection(slot: int) -> dict[str, Any] | None:
    projection_path = projection_path_for_slot(slot)
    if not projection_path.exists():
        return None
    with projection_path.open("r", encoding="utf-8") as projection_file:
        return json.load(projection_file)


def read_onchain_player_state(
    character: CharacterProfile,
) -> dict[str, Any] | None:
    if not character.character_mint:
        return None
    result = subprocess.run(
        [
            "node",
            "read-player-state.mjs",
            character.owner,
            character.character_mint,
        ],
        cwd=_solana_tools_dir(),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("Unable to read Solamon player state: %s", result.stderr)
        return None
    return json.loads(result.stdout)


def chain_slot_is_loadable(
    slot: int,
    character: CharacterProfile | None,
) -> bool:
    if character is None:
        return False
    onchain = read_onchain_player_state(character)
    if onchain is None:
        return False
    if not _has_onchain_save(onchain):
        return False
    parsed = parse_compact_uri(str(onchain.get("saveUri", "")))
    if parsed is not None:
        return int(parsed[0]) == int(slot)
    return slot == 1


def latest_chain_slot(
    max_slots: int,
    character: CharacterProfile | None,
) -> int | None:
    if character is None:
        return None
    onchain = read_onchain_player_state(character)
    if onchain is None:
        return None
    if not _has_onchain_save(onchain):
        return None
    save_hash = str(onchain.get("saveHash"))
    parsed = parse_compact_uri(str(onchain.get("saveUri", "")))
    if parsed is not None:
        return parsed[0]
    return 1 if save_hash else None


def load_onchain_save_data(character: CharacterProfile) -> SaveData | None:
    onchain = read_onchain_player_state(character)
    if onchain is None:
        return None
    if not _has_onchain_save(onchain):
        return None
    save_hash = str(onchain.get("saveHash"))
    parsed = parse_compact_uri(str(onchain.get("saveUri", "")))
    if parsed is not None:
        game_slot, base_save_hash = parsed
        base_save_data = _read_full_onchain_save(character, base_save_hash)
        if base_save_data is None:
            return None
        compact = _read_onchain_compact(character, save_hash, game_slot)
        if compact is None:
            return None
        return apply_compact_payload(base_save_data, compact)
    return _read_full_onchain_save(character, save_hash)


def _has_onchain_save(onchain: dict[str, Any]) -> bool:
    save_hash = str(onchain.get("saveHash") or "")
    if not save_hash or save_hash == "0" * 64:
        return False
    if int(onchain.get("saveVersion") or 0) <= 0:
        return False
    return True


def _read_full_onchain_save(
    character: CharacterProfile,
    save_hash: str,
) -> SaveData | None:
    result = subprocess.run(
        [
            "node",
            "read-save-blob.mjs",
            character.owner,
            character.character_mint,
            save_hash,
        ],
        cwd=_solana_tools_dir(),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("Unable to read Solamon save blob: %s", result.stderr)
        return None
    raw = json.loads(result.stdout)
    if raw is None:
        return None
    return SaveData.model_validate(raw)


def _read_onchain_compact(
    character: CharacterProfile,
    save_hash: str,
    game_slot: int,
) -> dict[str, Any] | None:
    result = subprocess.run(
        [
            "node",
            "read-compact-save.mjs",
            character.owner,
            character.character_mint,
            save_hash,
            str(game_slot),
        ],
        cwd=_solana_tools_dir(),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning("Unable to read Solamon compact save: %s", result.stderr)
        return None
    raw = json.loads(result.stdout)
    if raw is None:
        return None
    return raw


def _solana_tools_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "tools" / "solana"
