# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
"""This module contains the Tuxemon server and client."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, Any

from tuxemon.db import Direction
from tuxemon.chain.skins import available_character_skins
from tuxemon.entity.npc import NPC
from tuxemon.platform.const.sizes import PLAYER_NPC
from tuxemon.session import local_session
from tuxemon.states import world_state as world

if TYPE_CHECKING:
    from tuxemon.base_client import BaseClient
    from tuxemon.item.item import Item
    from tuxemon.monster.monster import Monster

logger = logging.getLogger(__name__)


class EventType(str, Enum):
    PUSH_SELF = "PUSH_SELF"
    CLIENT_MOVE_START = "CLIENT_MOVE_START"
    CLIENT_MAP_UPDATE = "CLIENT_MAP_UPDATE"
    CLIENT_MOVE_COMPLETE = "CLIENT_MOVE_COMPLETE"
    CLIENT_KEYDOWN = "CLIENT_KEYDOWN"
    CLIENT_KEYUP = "CLIENT_KEYUP"
    CLIENT_FACING = "CLIENT_FACING"
    CLIENT_INTERACTION = "CLIENT_INTERACTION"
    CLIENT_RESPONSE = "CLIENT_RESPONSE"
    CLIENT_START_BATTLE = "CLIENT_START_BATTLE"
    CLIENT_CHAT = "CLIENT_CHAT"
    CLIENT_DISCONNECTED = "CLIENT_DISCONNECTED"
    PING = "PING"
    SERVER_SHUTDOWN = "SERVER_SHUTDOWN"


@dataclass
class CharData:
    """Represents the mutable state of a character payload."""

    tile_pos: tuple[
        int, int
    ]  # (x, y) position of the character on the map grid
    name: str  # Character's display name
    facing: Direction  # Direction the character is currently facing (e.g., up, down)
    running: bool = False
    skin: str = "adventurer"

    def copy(self, **updates: Any) -> CharData:
        return replace(self, **updates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tile_pos": self.tile_pos,
            "name": self.name,
            "facing": self.facing.value,
            "running": self.running,
            "skin": self.skin,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> CharData:
        facing_raw = str(data.get("facing", Direction.DOWN.value))
        facing = Direction(facing_raw.lower())
        skin = str(data.get("skin") or "adventurer")[:48]
        if skin not in available_character_skins():
            skin = "adventurer"
        return CharData(
            tile_pos=tuple(data.get("tile_pos", (0, 0))),
            name=str(data.get("name", ""))[:32],
            facing=facing,
            running=bool(data.get("running", False)),
            skin=skin,
        )


@dataclass
class EventData:
    """Represents the network event payload sent between client and server."""

    type: EventType  # The type of event (e.g., CLIENT_KEYDOWN, PUSH_SELF)
    event_number: int  # Sequence number for tracking event order
    cuuid: str | None = (
        None  # Unique client identifier (who sent or triggered the event)
    )
    direction: str | None = (
        None  # Intended movement direction (e.g., "up", "left") — used in movement events
    )
    interaction: str | None = (
        None  # Type of interaction (e.g., "talk", "battle") — used in interaction events
    )
    map_name: str | None = None  # Name of the map where the event occurred
    char_dict: CharData | None = (
        None  # Snapshot of character state (position, facing, inventory, etc.)
    )
    kb_key: str | None = (
        None  # Key pressed or released (e.g., "SHIFT", "up") — used in input events
    )
    target: str | None = (
        None  # Target client or entity for interactions or combat
    )
    owner: str | None = None
    character_mint: str | None = None
    response: Any | None = (
        None  # Optional response payload (e.g., dialogue result, battle outcome)
    )
    message: str | None = None

    def copy(self, **updates: Any) -> EventData:
        return replace(self, **updates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.name,
            "event_number": self.event_number,
            "cuuid": self.cuuid,
            "direction": self.direction,
            "interaction": self.interaction,
            "map_name": self.map_name,
            "char_dict": self.char_dict.to_dict() if self.char_dict else None,
            "kb_key": self.kb_key,
            "target": self.target,
            "response": self.response,
            "owner": self.owner,
            "character_mint": self.character_mint,
            "message": self.message,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> EventData:
        return EventData(
            type=EventType[data["type"]],
            event_number=data["event_number"],
            cuuid=data.get("cuuid"),
            direction=data.get("direction"),
            interaction=data.get("interaction"),
            map_name=data.get("map_name"),
            char_dict=(
                CharData.from_dict(data["char_dict"])
                if data.get("char_dict")
                else None
            ),
            kb_key=data.get("kb_key"),
            target=data.get("target"),
            response=data.get("response"),
            owner=data.get("owner"),
            character_mint=data.get("character_mint"),
            message=data.get("message"),
        )


def populate_client(
    cuuid: str,
    event_data: EventData,
    game: BaseClient,
    registry: dict[str, dict[str, Any]],
) -> NPC:
    """
    Creates an NPC to represent the client character and adds the information
    to the registry.

    Parameters:
        cuuid (str): The unique user identification number for the client.
        event_data (EventData): Event information sent by the client,
            containing details about the client character (e.g., sprite name,
            map name, and character dictionary).
        game: The game control object for managing the server or client.
        registry: A registry containing client information on the server or client.

    Returns:
        The sprite representing the client character.
    """
    if event_data.char_dict is None or event_data.map_name is None:
        raise ValueError(f"Incomplete event data for client {cuuid}")

    char_data = event_data.char_dict
    slug = f"remote_{cuuid[:8].replace('-', '_')}"
    char_name = char_data.name or slug
    tile_pos_x, tile_pos_y = char_data.tile_pos

    char = local_session.client.npc_manager.get_npc(slug)
    if char is None:
        char = NPC.create(local_session, PLAYER_NPC)
        char.slug = slug
        char.ignore_collisions = True
        local_session.client.npc_manager.place_npc_on_map(
            char, event_data.map_name, tile_pos_x, tile_pos_y
        )

    char.name = char_name
    char.is_player = False
    char.ignore_collisions = True
    char._last_tile_pos = char.tile_pos
    char.interactions = ["TRADE", "DUEL"]

    # Update the registry with the client sprite and map name
    registry[cuuid]["sprite"] = char
    registry[cuuid]["map_name"] = event_data.map_name

    return char


def update_client(
    sprite: NPC, char_data: CharData | None, game: BaseClient
) -> None:
    """Corrects character location when it changes map or loses sync.

    Updates a client's character information, correcting its location and
    synchronization when switching maps or when data becomes out of sync.

    Parameters:
        sprite: The NPC object representing the local client's character
            (stored in the registry).
        char_data: A CharData object containing updated character state (e.g., tile position, facing).
        game: The game control object (server or client) for managing the game's state.
    """
    if char_data is None:
        return

    sprite.name = char_data.name or sprite.name
    if sprite.appearance_manager.state.sprite_name != char_data.skin:
        sprite.appearance_manager.update(char_data.skin, char_data.skin)
    sprite.set_facing(char_data.facing)
    sprite.complete_tile_entry(char_data.tile_pos)
