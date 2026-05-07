# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import final

from tuxemon.chain.fees import require_player_sol_for_chain
from tuxemon.event.eventaction import EventAction
from tuxemon.session import Session
from tuxemon.teleporter import TeleportFaint
from tuxemon.tools import parse_flag

logger = logging.getLogger(__name__)

SOLAMON_FALLBACK_FAINT_MAP = "spyder_bedroom.tmx"
SOLAMON_FALLBACK_FAINT_X = 6
SOLAMON_FALLBACK_FAINT_Y = 5


@final
@dataclass
class TeleportFaintAction(EventAction):
    """
    Teleport the player to the point in the teleport_faint variable.

    Usually used to teleport to the last visited Tuxcenter, as when
    all monsters in the party faint.

    Script usage:
        .. code-block::

            teleport_faint <character>,<healing>[,trans_time][,rgb]

    Script parameters:
        character: Either "player" or npc slug name (e.g. "npc_maple").
        healing: Trigger healing string flag ("true", "1", "yes" for True),
        trans_time: Transition time in seconds - default 0.3
        rgb: color (eg red > 255,0,0 > 255:0:0) - default rgb(0,0,0)

    eg: "teleport_faint player,true,3"
    eg: "teleport_faint player,true,3,255:0:0:50" (red)
    """

    name = "teleport_faint"
    character: str
    healing: str
    trans_time: float | None = None
    rgb: str | None = None

    def start(self, session: Session) -> None:
        character = session.client.get_npc(self.character)
        if character is None:
            logger.error(f"{self.character} not found")
            self.stop()
            return

        healing = parse_flag(self.healing)
        if healing and not require_player_sol_for_chain(
            session, "battle loss recovery"
        ):
            self.stop()
            return

        client = session.client
        if getattr(client, "_solamon_faint_recovery_in_progress", False):
            self.stop()
            return

        current_state = client.current_state
        if current_state and current_state.name == "DialogState":
            client.remove_state_by_name("DialogState")

        if character.teleport_faint.is_default():
            if healing:
                logger.warning(
                    "The teleport_faint variable has not been set; "
                    "using the Solamon fallback recovery target."
                )
                character.teleport_faint = TeleportFaint(
                    SOLAMON_FALLBACK_FAINT_MAP,
                    SOLAMON_FALLBACK_FAINT_X,
                    SOLAMON_FALLBACK_FAINT_Y,
                )
                teleport = character.teleport_faint
            else:
                logger.error(
                    "The teleport_faint variable has not been set, use "
                    "'set_teleport_faint'."
                )
                self.stop()
                return
        else:
            teleport = character.teleport_faint

        if teleport.is_default():
            logger.error(
                "The teleport_faint variable has not been set, use 'set_teleport_faint'."
            )
            self.stop()
            return

        action = client.event_engine

        if healing:
            setattr(client, "_solamon_pending_faint_recovery", True)
            setattr(client, "_solamon_faint_recovery_in_progress", True)

        action.execute_action(
            "transition_teleport",
            [
                self.character,
                teleport.map_name,
                teleport.x,
                teleport.y,
                self.trans_time,
                self.rgb,
            ],
        )
