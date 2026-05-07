# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from tuxemon.chain.character import CharacterProfile
from tuxemon.chain.wallet import DEFAULT_WALLET_PATH, UnlockedWallet
from tuxemon.user_config import CONFIG


@dataclass
class ChainSession:
    wallet: UnlockedWallet | None = None
    character: CharacterProfile | None = None

    @property
    def is_unlocked(self) -> bool:
        return self.wallet is not None

    @property
    def public_key(self) -> str | None:
        return self.wallet.public_key if self.wallet else None

    def set_wallet(self, wallet: UnlockedWallet) -> None:
        self.wallet = wallet

    @property
    def has_character(self) -> bool:
        if self.character is None:
            return False
        return self.character.character_mint_status == "minted-devnet"

    def set_character(self, character: CharacterProfile) -> None:
        self.character = character

    def clear(self) -> None:
        self.wallet = None
        self.character = None


def configured_wallet_path() -> Path:
    if CONFIG.solana_wallet_path:
        return Path(CONFIG.solana_wallet_path).expanduser()
    return DEFAULT_WALLET_PATH
