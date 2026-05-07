# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

from pygame_menu.menu import Menu

from tuxemon.chain.character import (
    discover_character_profile,
    load_character_profile,
)
from tuxemon.chain.session import configured_wallet_path
from tuxemon.chain.wallet import (
    WalletExistsError,
    WalletUnlockError,
    create_wallet,
    unlock_wallet,
)
from tuxemon.menu.menu import PygameMenuState
from tuxemon.menu.transitions import PopInClamped
from tuxemon.tools import open_dialog

if TYPE_CHECKING:
    from tuxemon.base_client import BaseClient


class SolamonWalletState(PygameMenuState):
    """Gate chain-enabled game entry behind an unlocked Solana signer."""

    name: ClassVar[str] = "SolamonWalletState"
    shrink_to_items = True

    def __init__(
        self,
        client: BaseClient,
        on_ready: Callable[[], None],
        **kwargs: Any,
    ) -> None:
        self.on_ready = on_ready
        super().__init__(client=client, transition=PopInClamped(), **kwargs)
        self.add_menu_items(self.menu)

    def add_menu_items(self, menu: Menu) -> None:
        wallet_path = configured_wallet_path()
        if wallet_path.exists():
            menu.add.button("UNLOCK WALLET", self.ask_unlock)
        else:
            menu.add.button("CREATE WALLET", self.ask_create)
        menu.add.button("BACK", self.client.pop_state)

    def _finish(self) -> None:
        owner = self.client.chain_session.public_key
        if owner:
            profile = load_character_profile(owner)
            if profile is None or profile.character_mint_status != "minted-devnet":
                profile = discover_character_profile(owner)
            if profile and profile.character_mint_status == "minted-devnet":
                self.client.chain_session.set_character(profile)
        self.client.pop_state(self)
        self.on_ready()

    def _show_error(self, message: str) -> None:
        open_dialog(self.client, [message], dialog_speed="max")

    def ask_unlock(self) -> None:
        self.client.push_state(
            "InputMenu",
            prompt="Wallet password",
            callback=self.unlock,
            char_limit=64,
        )

    def ask_create(self) -> None:
        self.client.push_state(
            "InputMenu",
            prompt="New wallet password",
            callback=self._confirm_create,
            char_limit=64,
        )

    def _confirm_create(self, password: str) -> None:
        self.client.push_state(
            "InputMenu",
            prompt="Confirm password",
            callback=lambda confirmation: self.create(password, confirmation),
            char_limit=64,
        )

    def create(self, password: str, confirmation: str) -> None:
        if password != confirmation:
            self._show_error("Wallet passwords do not match.")
            return

        try:
            encrypted = create_wallet(password, configured_wallet_path())
            wallet = unlock_wallet(password, configured_wallet_path())
        except WalletExistsError:
            self._show_error("Wallet already exists.")
            return
        except WalletUnlockError:
            self._show_error("Wallet was created but could not be unlocked.")
            return

        self.client.chain_session.set_wallet(wallet)
        self._finish()

    def unlock(self, password: str) -> None:
        try:
            wallet = unlock_wallet(password, configured_wallet_path())
        except FileNotFoundError:
            self._show_error("No wallet exists yet.")
            return
        except WalletUnlockError:
            self._show_error("Invalid wallet password.")
            return

        self.client.chain_session.set_wallet(wallet)
        self._finish()
