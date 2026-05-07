# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import final

from tuxemon.chain.autosave import auto_save_chain_state
from tuxemon.chain.fees import require_player_sol_for_chain
from tuxemon.database.runtime import db
from tuxemon.event.eventaction import EventAction
from tuxemon.monster.monster import Monster
from tuxemon.session import Session

logger = logging.getLogger(__name__)

MISSING_MONSTER_VARIABLE_FALLBACKS = {
    "billie_choice": "budaye",
}


@final
@dataclass
class AddMonsterAction(EventAction):
    """
    Add a monster to the specified trainer's party if there is room.

    Script usage:
        .. code-block::

            add_monster <mon_slug>,<mon_level>[,npc_slug][,exp_mod][,money_mod]

    Script parameters:
        mon_slug: Monster slug to look up in the monster database or name variable
            where it's stored the mon_slug
        mon_level: Level of the added monster.
        npc_slug: Slug of the trainer that will receive the monster. It
            defaults to the current player.
        exp_mod: Experience modifier
        money_mod: Money modifier
    """

    name = "add_monster"
    monster_slug: str
    monster_level: int
    npc_slug: str | None = None
    exp: float | None = None
    money: float | None = None

    def start(self, session: Session) -> None:
        player = session.player
        self.npc_slug = self.npc_slug or "player"
        trainer = session.client.get_npc(self.npc_slug)
        if not trainer:
            raise ValueError(f"NPC '{self.npc_slug}' not found")

        if self.monster_slug not in db.database["monster"]:
            if player.game_variables.has(self.monster_slug):
                monster_slug = player.game_variables.get(self.monster_slug)
            elif self.monster_slug in MISSING_MONSTER_VARIABLE_FALLBACKS:
                monster_slug = MISSING_MONSTER_VARIABLE_FALLBACKS[self.monster_slug]
                logger.warning(
                    "%s is missing; using %s for %s.",
                    self.monster_slug,
                    monster_slug,
                    trainer.slug,
                )
            else:
                logger.warning(
                    "%s doesn't exist as a monster or variable; skipping add_monster for %s.",
                    self.monster_slug,
                    trainer.slug,
                )
                self.stop()
                return
        else:
            monster_slug = self.monster_slug

        if monster_slug not in db.database["monster"]:
            logger.warning(
                "%s resolved to unknown monster %s; skipping add_monster for %s.",
                self.monster_slug,
                monster_slug,
                trainer.slug,
            )
            self.stop()
            return

        if trainer.is_player and not session.chain_autosave_suppressed:
            if not require_player_sol_for_chain(session, f"monster added {monster_slug}"):
                self.stop()
                return

        is_first_player_monster = trainer.is_player and len(trainer.monsters) == 0
        monster = Monster.spawn_base(monster_slug, self.monster_level)

        if self.exp is not None:
            monster.set_experience_modifier(self.exp)
        if self.money is not None:
            monster.money_modifier = self.money

        trainer.party.add_monster(monster, len(trainer.monsters))
        trainer.tuxepedia.register_caught(monster.slug)
        player.game_variables.set(self.name, monster.instance_id.hex)
        if trainer.is_player:
            reason = (
                f"starter chosen {monster.slug}"
                if is_first_player_monster
                else f"monster added {monster.slug}"
            )
            auto_save_chain_state(session, reason)
        self.stop()
