# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

from dataclasses import dataclass

from tuxemon.chain.wallet import UnlockedWallet


@dataclass(frozen=True)
class SignedTransactionMessage:
    public_key: str
    message: bytes
    signature: bytes


def sign_transaction_message(
    wallet: UnlockedWallet,
    transaction_message: bytes,
) -> SignedTransactionMessage:
    return SignedTransactionMessage(
        public_key=wallet.public_key,
        message=transaction_message,
        signature=wallet.sign_transaction(transaction_message),
    )
