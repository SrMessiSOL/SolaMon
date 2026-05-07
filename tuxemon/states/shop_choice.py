# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from pygame_menu.locals import ALIGN_CENTER
from pygame_menu.menu import Menu

from tuxemon.locale.locale import T
from tuxemon.menu.menu import PygameMenuState
from tuxemon.platform.const import buttons
from tuxemon.platform.const.graphics import BG_MISSIONS

if TYPE_CHECKING:
    from tuxemon.base_client import BaseClient
    from tuxemon.entity.npc import NPC
    from tuxemon.platform.events import PlayerInput
    from tuxemon.session import Session


class ShopChoiceState(PygameMenuState):
    """
    Minimal state for choosing between Buy / Sell.
    """

    name: ClassVar[str] = "ShopChoiceState"

    def __init__(
        self,
        client: BaseClient,
        session: Session,
        npc: NPC,
        mode: str,
        **kwargs: Any,
    ) -> None:
        self.session = session
        self.npc = npc
        self.mode = mode
        self.return_map = session.client.get_map_name()
        self.return_tile = tuple(int(pos) for pos in session.player.tile_pos)
        self.return_facing = session.player.facing

        if npc.economy is None:
            return

        self.economy = npc.economy

        width, height = client.context.resolution

        super().__init__(
            client=client,
            height=int(height * 0.4),
            width=int(width * 0.4),
            **kwargs,
        )

        theme = self._setup_theme(BG_MISSIONS)
        theme.widget_alignment = ALIGN_CENTER
        self._menu_config["theme"] = theme
        self._build_menu(self.menu)
        self.reset_theme()

    def _build_menu(self, menu: Menu) -> None:
        if self.mode == "item":
            can_sell = bool(self.session.player.items)
        else:
            can_sell = bool(self.session.player.monsters)

        menu.add.button(T.translate("buy").upper(), action=self._buy)

        if can_sell:
            menu.add.button(T.translate("sell").upper(), action=self._sell)

    def _buy(self) -> None:
        if self.mode == "item":
            self.session.client.push_state(
                "ShopItemBuyMenuState",
                buyer=self.session.player,
                seller=self.npc,
                economy=self.economy,
            )
        else:
            self.session.client.push_state(
                "ShopMonsterBuyMenuState",
                buyer=self.session.player,
                seller=self.npc,
                economy=self.economy,
            )

    def _sell(self) -> None:
        if self.mode == "item":
            self.session.client.push_state(
                "ShopItemSellMenuState",
                buyer=self.npc,
                seller=self.session.player,
                economy=self.economy,
            )
        else:
            self.session.client.push_state(
                "ShopMonsterSellMenuState",
                buyer=self.npc,
                seller=self.session.player,
                economy=self.economy,
            )

    def process_event(self, event: PlayerInput) -> PlayerInput | None:
        if event.button in (buttons.B, buttons.BACK) and event.pressed:
            self._restore_player_return()
            self.client.pop_state(self)
            self.session.client.remove_state_by_name("DialogState")
            return None
        return super().process_event(event)

    def shutdown(self) -> None:
        super().shutdown()
        if not any(
            getattr(state, "name", "").startswith("Shop")
            for state in self.client.active_states
        ):
            self._restore_player_return()
            self.client.event_engine.resume()

    def _restore_player_return(self) -> None:
        player = self.session.player
        current_map = self.session.client.get_map_name()
        on_map_player = self.session.client.npc_manager.get_npc(player.slug)
        if (
            current_map != self.return_map
            or on_map_player is None
            or tuple(int(pos) for pos in player.tile_pos) != self.return_tile
        ):
            self.session.client.map_transition.change_map(self.return_map)
            self.session.client.npc_manager.place_npc_on_map(
                player,
                self.return_map,
                self.return_tile[0],
                self.return_tile[1],
            )
        player.facing = self.return_facing
