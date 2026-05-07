# SPDX-License-Identifier: GPL-3.0
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Mapping

from tuxemon.chain.save_blob import hash_payload
from tuxemon.save_system.save_state import SaveData

COMPACT_SCHEMA = "solamon-compact-v1"
def build_compact_payload(
    save_data: SaveData,
    base_save_hash: str,
    *,
    include_structure: bool = False,
    include_item_structure: bool = False,
    include_monster_structure: bool = False,
) -> dict[str, Any]:
    raw = save_data.model_dump(mode="json")
    npc_state = raw.get("npc_state") or {}
    money = npc_state.get("money") or {}
    tile_x, tile_y = (npc_state.get("tile_pos") or [0, 0])[:2]
    items = npc_state.get("items") or []
    monsters = npc_state.get("monsters") or []
    game_variables = npc_state.get("game_variables") or {}
    payload = {
        "schema": COMPACT_SCHEMA,
        "base": base_save_hash,
        "loc": {
            "map_id": npc_state.get("current_map") or "",
            "tile_x": int(tile_x),
            "tile_y": int(tile_y),
            "facing": npc_state.get("facing"),
            "position": npc_state.get("position"),
        },
        "cur": {
            "w": int(money.get("money", 0)),
            "b": int(money.get("bank_account", 0)),
        },
        "vars": _compact_game_variables(game_variables),
        "items": [
            {
                "id": item.get("instance_id"),
                "slug": item.get("slug"),
                "q": item.get("quantity", 1),
                "w": item.get("wear", 0),
            }
            for item in items
        ],
        "mons": [
            {
                "id": monster.get("instance_id"),
                "slug": monster.get("slug"),
                "hp": monster.get("current_hp"),
                "lv": monster.get("level"),
                "xp": monster.get("total_experience"),
                "st": monster.get("status", []),
                "tp": monster.get("training_points", {}),
                "mv": [
                    {
                        "a": move.get("attempts", 0),
                        "s": move.get("successes", 0),
                        "f": move.get("failures", 0),
                    }
                    for move in monster.get("moves") or []
                ],
            }
            for monster in monsters
        ],
    }
    include_item_structure = include_item_structure or include_structure
    include_monster_structure = include_monster_structure or include_structure
    if include_item_structure:
        payload["full_items"] = items
    if include_monster_structure:
        payload["full_mons"] = monsters
    payload["hash"] = compact_hash(payload)
    return payload


def _compact_game_variables(game_variables: Mapping[str, Any]) -> dict[str, Any]:
    # Story flags gate one-time battles and rewards, so compact saves must not
    # drop newly introduced map variables such as route2billie or gym flags.
    return {str(key): value for key, value in game_variables.items()}


def compact_hash(payload: Mapping[str, Any]) -> str:
    body = dict(payload)
    body.pop("hash", None)
    return hash_payload(body)


def compact_uri(game_slot: int, base_save_hash: str) -> str:
    return f"compact:{game_slot}:{base_save_hash}"


def parse_compact_uri(value: str) -> tuple[int, str] | None:
    parts = str(value).split(":")
    if len(parts) != 3 or parts[0] != "compact":
        return None
    return int(parts[1]), parts[2]


def apply_compact_payload(base_save_data: SaveData, payload: Mapping[str, Any]) -> SaveData:
    raw = deepcopy(base_save_data.model_dump(mode="json"))
    npc_state = raw.setdefault("npc_state", {})

    loc = payload.get("loc") or {}
    npc_state["current_map"] = loc.get("map_id") or npc_state.get("current_map")
    npc_state["tile_pos"] = [int(loc.get("tile_x", 0)), int(loc.get("tile_y", 0))]
    if loc.get("position") is not None:
        npc_state["position"] = loc["position"]
    else:
        npc_state["position"] = [float(npc_state["tile_pos"][0]), float(npc_state["tile_pos"][1])]
    if loc.get("facing"):
        npc_state["facing"] = loc["facing"]

    money = npc_state.setdefault("money", {})
    cur = payload.get("cur") or {}
    money["money"] = int(cur.get("w", money.get("money", 0)))
    money["bank_account"] = int(cur.get("b", money.get("bank_account", 0)))

    game_variables = npc_state.setdefault("game_variables", {})
    for key, value in (payload.get("vars") or {}).items():
        game_variables[str(key)] = value

    if payload.get("full_items") is not None:
        npc_state["items"] = deepcopy(payload.get("full_items") or [])
    else:
        items = npc_state.setdefault("items", [])
        for index, compact_item in enumerate(payload.get("items") or []):
            if index >= len(items):
                items.append(
                    {
                        "instance_id": compact_item.get("id"),
                        "slug": compact_item.get("slug") or compact_item.get("game_id") or "",
                        "quantity": compact_item.get("q", 1),
                        "wear": compact_item.get("w", 0),
                    }
                )
                continue
            item = items[index]
            item["quantity"] = compact_item.get("q", item.get("quantity", 1))
            item["wear"] = compact_item.get("w", item.get("wear", 0))

    if payload.get("full_mons") is not None:
        npc_state["monsters"] = deepcopy(payload.get("full_mons") or [])
    else:
        for index, compact_monster in enumerate(payload.get("mons") or []):
            if index >= len(npc_state.get("monsters") or []):
                continue
            monster = npc_state["monsters"][index]
            monster["current_hp"] = compact_monster.get("hp", monster.get("current_hp"))
            monster["level"] = compact_monster.get("lv", monster.get("level"))
            monster["total_experience"] = compact_monster.get(
                "xp", monster.get("total_experience")
            )
            monster["status"] = compact_monster.get("st", monster.get("status", []))
            monster["training_points"] = compact_monster.get(
                "tp", monster.get("training_points", {})
            )
            for move_index, compact_move in enumerate(compact_monster.get("mv") or []):
                if move_index >= len(monster.get("moves") or []):
                    continue
                move = monster["moves"][move_index]
                move["attempts"] = compact_move.get("a", move.get("attempts", 0))
                move["successes"] = compact_move.get("s", move.get("successes", 0))
                move["failures"] = compact_move.get("f", move.get("failures", 0))

    return SaveData.model_validate(raw)


def write_compact_payload_file(payload: Mapping[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as payload_file:
        json.dump(payload, payload_file, indent=2, sort_keys=True)
