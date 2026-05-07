# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import final

from tuxemon.chain.autosave import auto_save_chain_state
from tuxemon.chain.fees import require_player_sol_for_chain
from tuxemon.event.eventaction import EventAction
from tuxemon.formula import set_health
from tuxemon.session import Session
from tuxemon.tools import get_valid_uuid

logger = logging.getLogger(__name__)


@final
@dataclass
class SetMonsterHealthAction(EventAction):
    """
    Set the hp of a monster in the current player's party.

    Script usage:
        .. code-block::

            set_monster_health [variable][,health]

    Script parameters:
        variable: Name of the variable where to store the monster id. If no
            variable is specified, all monsters are healed.
        health: A float value between 0 and 1, which is the percent of max
            hp to be restored to. A int value, which is the number of HP
            to be restored to. If no health is specified, the hp is maxed
            out.
    """

    name = "set_monster_health"
    variable: str | None = None
    health: int | float | None = None

    def start(self, session: Session) -> None:
        player = session.player
        if not player.monsters:
            self.stop()
            return

        monster_health = 1.0 if self.health is None else self.health
        defer_chain_save = getattr(
            session.client, "_solamon_defer_heal_autosave", False
        )

        if self.variable is None:
            should_chain_save = (
                session.client.config.chain_enabled
                and not defer_chain_save
            )
            if should_chain_save and not require_player_sol_for_chain(
                session, "heal party"
            ):
                self.stop()
                return
            previous_monsters = [
                (mon, mon.current_hp, list(mon.status.get_statuses()))
                for mon in player.monsters
            ]
            for mon in player.monsters:
                set_health(mon, monster_health)
                if mon.is_fainted:
                    mon.status.apply_faint(session, mon)
            if should_chain_save:
                saved = auto_save_chain_state(session, "heal party")
                if not saved:
                    for mon, previous_hp, previous_status in previous_monsters:
                        mon.current_hp = previous_hp
                        mon.status.status = previous_status
                    logger.warning(
                        "Rolled back local party heal because chain save failed."
                    )
        else:
            monster_id = get_valid_uuid(player.game_variables, self.variable)
            if monster_id is None:
                logger.info(
                    f"No valid monster selected for variable '{self.variable}'"
                )
                self.stop()
                return  # Exit early if no valid UUID
            monster = session.client.get_monster_by_iid(monster_id)
            if monster is None:
                logger.error("Monster not found")
                self.stop()
                return
            should_chain_save = (
                session.client.config.chain_enabled
                and not defer_chain_save
            )
            if should_chain_save and not require_player_sol_for_chain(
                session, f"heal monster {monster.slug}"
            ):
                self.stop()
                return
            previous_hp = monster.current_hp
            previous_status = list(monster.status.get_statuses())
            set_health(monster, monster_health)
            if monster.is_fainted:
                monster.status.apply_faint(session, monster)
            if should_chain_save:
                saved = auto_save_chain_state(session, f"heal monster {monster.slug}")
                if not saved:
                    monster.current_hp = previous_hp
                    monster.status.status = previous_status
                    logger.warning(
                        "Rolled back local %s heal because chain save failed.",
                        monster.slug,
                    )
        self.stop()
