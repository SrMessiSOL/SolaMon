# SPDX-License-Identifier: GPL-3.0
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar

from pygame_menu.menu import Menu

from tuxemon.chain.wallet_info import (
    copy_to_clipboard,
    export_keypair,
    read_wallet_info,
    withdraw_sol,
)
from tuxemon.chain.loading import show_blockchain_loading
from tuxemon.menu.menu import PygameMenuState
from tuxemon.menu.transitions import PopInClamped
from tuxemon.tools import open_dialog

if TYPE_CHECKING:
    from tuxemon.base_client import BaseClient

logger = logging.getLogger(__name__)


class SolamonWalletManageState(PygameMenuState):
    """In-game Solamon wallet tools."""

    name: ClassVar[str] = "SolamonWalletManageState"
    shrink_to_items = True

    def __init__(
        self,
        client: BaseClient,
        **kwargs: Any,
    ) -> None:
        super().__init__(client=client, transition=PopInClamped(), **kwargs)
        self.add_menu_items(self.menu)

    def add_menu_items(self, menu: Menu) -> None:
        menu.add.button("BALANCE", self.show_balance)
        menu.add.button("DEPOSIT SOL", self.deposit)
        menu.add.button("WITHDRAW SOL", self.ask_withdraw_address)
        menu.add.button("COPY ADDRESS", self.copy_address)
        menu.add.button("EXPORT KEYPAIR", self.export)
        menu.add.button("BACK", self.client.pop_state)

    def _owner(self) -> str | None:
        return self.client.chain_session.public_key

    def _wallet(self):
        return self.client.chain_session.wallet

    def _show(self, lines: list[str]) -> None:
        open_dialog(self.client, lines, dialog_speed="max")

    def show_balance(self) -> None:
        owner = self._owner()
        if owner is None:
            self._show(["No Solamon wallet is unlocked."])
            return
        try:
            show_blockchain_loading(self.client)
            info = read_wallet_info(owner)
        except Exception:
            logger.error("Unable to read Solamon wallet balance.", exc_info=True)
            self._show(["Unable to read wallet balance."])
            return
        self._show(
            [
                f"Address: {info['owner']}",
                f"SOL: {float(info['sol']):.6f}",
                f"{info['tokenSymbol']}: {info['tokenUiAmount']}",
            ]
        )

    def deposit(self) -> None:
        owner = self._owner()
        if owner is None:
            self._show(["No Solamon wallet is unlocked."])
            return
        try:
            copy_to_clipboard(owner)
            copied = "Address copied to clipboard."
        except RuntimeError:
            copied = "Copy failed. Use the address shown here."
        self._show(
            [
                "Deposit SOL for transaction fees to this address:",
                owner,
                copied,
            ]
        )

    def copy_address(self) -> None:
        owner = self._owner()
        if owner is None:
            self._show(["No Solamon wallet is unlocked."])
            return
        try:
            copy_to_clipboard(owner)
        except RuntimeError:
            logger.error("Unable to copy Solamon wallet address.", exc_info=True)
            self._show(["Unable to copy wallet address.", owner])
            return
        self._show(["Wallet address copied.", owner])

    def ask_withdraw_address(self) -> None:
        self.client.push_state(
            "InputMenu",
            prompt="Withdraw to address (Ctrl+V or PASTE)",
            callback=self.ask_withdraw_amount,
            char_limit=64,
        )

    def ask_withdraw_amount(self, destination: str) -> None:
        destination = destination.strip()
        if not destination:
            self._show(["Withdraw address is required."])
            return
        self.client.push_state(
            "InputMenu",
            prompt="SOL amount",
            callback=lambda amount: self.withdraw(destination, amount),
            char_limit=24,
        )

    def withdraw(self, destination: str, amount: str) -> None:
        wallet = self._wallet()
        if wallet is None:
            self._show(["No Solamon wallet is unlocked."])
            return
        try:
            show_blockchain_loading(self.client, "Loading blockchain...")
            result = withdraw_sol(wallet, destination, amount.strip())
        except Exception as exc:
            logger.error("Unable to withdraw SOL.", exc_info=True)
            self._show(["Withdraw failed.", str(exc)[:180]])
            return
        self._show(
            [
                "Withdraw sent.",
                f"SOL: {result['amountSol']}",
                f"Signature: {result['signature']}",
            ]
        )

    def export(self) -> None:
        wallet = self._wallet()
        if wallet is None:
            self._show(["No Solamon wallet is unlocked."])
            return
        try:
            path = export_keypair(wallet)
        except Exception:
            logger.error("Unable to export Solamon keypair.", exc_info=True)
            self._show(["Export failed."])
            return
        self._show(
            [
                "Keypair exported.",
                str(path),
                "Keep this file private. Anyone with it can spend from this wallet.",
            ]
        )
