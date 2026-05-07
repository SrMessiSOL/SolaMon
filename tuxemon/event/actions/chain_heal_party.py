# SPDX-License-Identifier: GPL-3.0
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import final

from tuxemon.chain.autosave import auto_save_chain_currency_transfer
from tuxemon.chain.fees import require_player_sol_for_chain
from tuxemon.event.eventaction import EventAction
from tuxemon.formula import set_health
from tuxemon.session import Session

logger = logging.getLogger(__name__)


@final
@dataclass
class ChainHealPartyAction(EventAction):
    """
    Heal the player's party and spend SLMN in the same chain save flow.

    Script usage:
        chain_heal_party <cost_variable>
    """

    name = "chain_heal_party"
    cost_variable: str

    def start(self, session: Session) -> None:
        player = session.player
        raw_cost = player.game_variables.get(self.cost_variable, 0)
        try:
            cost = abs(int(raw_cost))
        except (TypeError, ValueError):
            logger.error("Invalid heal cost variable %s: %r", self.cost_variable, raw_cost)
            self.stop()
            return

        if cost <= 0 or not player.monsters:
            self.stop()
            return

        money_manager = player.money_controller.money_manager
        previous_money = money_manager.get_money()
        previous_monsters = [
            (monster, monster.current_hp, list(monster.status.get_statuses()))
            for monster in player.monsters
        ]

        if previous_money < cost:
            logger.warning("Cannot heal party: %s SLMN available, %s needed.", previous_money, cost)
            self.stop()
            return
        if not require_player_sol_for_chain(session, "paid healing party"):
            self.stop()
            return

        money_manager.remove_money(cost)
        for monster in player.monsters:
            set_health(monster, 1.0)
            monster.status.clear_status(session)
            monster.status.status.clear()

        saved = auto_save_chain_currency_transfer(
            session,
            "paid healing party",
            direction="spend",
            amount=cost,
        )
        if not saved:
            money_manager.set_money(previous_money)
            for monster, previous_hp, previous_status in previous_monsters:
                monster.current_hp = previous_hp
                monster.status.status = previous_status
            logger.warning("Rolled back local party heal because chain save failed.")

        self.stop()
