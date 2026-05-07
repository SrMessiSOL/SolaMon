# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(raw: bytes) -> str:
    """Encode bytes using Bitcoin/Solana base58 without extra dependencies."""
    value = int.from_bytes(raw, "big")
    encoded = ""

    while value:
        value, remainder = divmod(value, 58)
        encoded = ALPHABET[remainder] + encoded

    leading_zeroes = len(raw) - len(raw.lstrip(b"\0"))
    return ("1" * leading_zeroes) + (encoded or "1")
