# SPDX-License-Identifier: GPL-3.0
from __future__ import annotations

import logging
from typing import Any, ClassVar

from pygame_menu.menu import Menu

from tuxemon.chain.fees import (
    LAMPORTS_PER_SOL,
    MIN_CHAIN_ACTION_LAMPORTS,
    player_sol_balance_ok,
    release_chain_fee_block,
)
from tuxemon.chain.loading import show_blockchain_loading
from tuxemon.chain.wallet_info import copy_to_clipboard
from tuxemon.menu.menu import PygameMenuState
from tuxemon.menu.transitions import PopInClamped
from tuxemon.platform.const import buttons
from tuxemon.session import local_session
from tuxemon.tools import open_dialog

logger = logging.getLogger(__name__)


class SolamonBalanceBlockedState(PygameMenuState):
    """Blocks on-chain play until the player vault has enough SOL."""

    name: ClassVar[str] = "SolamonBalanceBlockedState"
    shrink_to_items = True

    def __init__(
        self,
        client,
        *,
        reason: str = "chain transaction",
        detail: str = "",
        address: str = "",
        minimum_lamports: int = MIN_CHAIN_ACTION_LAMPORTS,
        **kwargs: Any,
    ) -> None:
        self.reason = reason
        self.detail = detail
        self.address = address
        self.minimum_lamports = minimum_lamports
        self.status_label = None
        super().__init__(client=client, transition=PopInClamped(), **kwargs)
        self.escape_key_exits = False
        self.add_menu_items(self.menu)

    def add_menu_items(self, menu: Menu) -> None:
        minimum = self.minimum_lamports / LAMPORTS_PER_SOL
        menu.add.label("SOL REQUIRED")
        menu.add.label(self.detail or "Deposit SOL to continue.")
        menu.add.label(f"Need at least {minimum:.3f} SOL.")
        if self.address:
            menu.add.label(self.address[:16] + "..." + self.address[-8:])
        self.status_label = menu.add.label("Press X to copy the address.")
        menu.add.button("REFRESH BALANCE", self.refresh)
        menu.add.button("COPY ADDRESS", self.copy_address)

    def refresh(self) -> None:
        if not self.address:
            open_dialog(self.client, ["No player vault address available."], dialog_speed="max")
            return
        try:
            show_blockchain_loading(self.client)
            ok, balance = player_sol_balance_ok(
                self.address,
                minimum_lamports=self.minimum_lamports,
            )
        except Exception:
            logger.error("Unable to refresh SOL balance.", exc_info=True)
            open_dialog(self.client, ["Unable to refresh SOL balance."], dialog_speed="max")
            return

        sol = balance / LAMPORTS_PER_SOL
        if not ok:
            self._set_status(f"Current vault balance: {sol:.6f} SOL.")
            return

        release_chain_fee_block(local_session)
        self.client.pop_state(self)
        open_dialog(
            self.client,
            [f"Vault funded: {sol:.6f} SOL.", "You can continue playing."],
            dialog_speed="max",
        )

    def copy_address(self) -> None:
        if not self.address:
            open_dialog(self.client, ["No player vault address available."], dialog_speed="max")
            return
        try:
            copy_to_clipboard(self.address)
        except RuntimeError:
            open_dialog(
                self.client,
                ["Copy failed. Use the address shown here.", self.address],
                dialog_speed="max",
            )
            return
        open_dialog(
            self.client,
            ["Player vault address copied.", self.address],
            dialog_speed="max",
        )

    def _set_status(self, text: str) -> None:
        label = self.status_label
        if label is None:
            return
        setter = getattr(label, "set_title", None)
        if callable(setter):
            setter(text)

    def process_event(self, event):
        if event.button in (buttons.B, buttons.BACK):
            self._set_status("Fund the vault, then refresh balance.")
            return None
        if event.button == buttons.X:
            self.copy_address()
            return None
        return super().process_event(event)
