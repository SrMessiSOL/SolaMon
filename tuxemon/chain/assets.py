# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ChainAssetKind(StrEnum):
    CURRENCY = "currency"
    ITEM = "item"
    MONSTER = "monster"
    BADGE = "badge"


@dataclass(frozen=True)
class SplCurrency:
    """The fungible SPL token used as the game's money."""

    mint: str
    decimals: int
    symbol: str


@dataclass(frozen=True)
class NftCollection:
    """A verified NFT collection used by one game asset class."""

    mint: str
    kind: ChainAssetKind
    name: str


@dataclass(frozen=True)
class ChainAssetConfig:
    currency: SplCurrency
    item_collection: NftCollection
    monster_collection: NftCollection
    badge_collection: NftCollection
