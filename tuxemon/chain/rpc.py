# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from tuxemon.user_config import CONFIG


class SolanaRpcError(RuntimeError):
    """Raised when a Solana RPC request fails."""


@dataclass(frozen=True)
class SplTokenBalance:
    owner: str
    mint: str
    amount: int
    decimals: int
    ui_amount: float


@dataclass(frozen=True)
class OwnedNft:
    mint: str
    owner: str
    collection: str | None
    name: str | None
    uri: str | None


class SolanaRpcClient:
    def __init__(self, rpc_url: str | None = None) -> None:
        self.rpc_url = rpc_url or CONFIG.solana_rpc_url

    def _request(self, method: str, params: list[Any]) -> Any:
        response = requests.post(
            self.rpc_url,
            json={
                "jsonrpc": "2.0",
                "id": "solamon",
                "method": method,
                "params": params,
            },
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            raise SolanaRpcError(str(body["error"]))
        return body["result"]

    def get_sol_balance(self, owner: str) -> int:
        result = self._request(
            "getBalance",
            [owner, {"commitment": "confirmed"}],
        )
        return int(result.get("value", 0))

    def get_spl_token_balance(
        self,
        owner: str,
        mint: str | None = None,
    ) -> SplTokenBalance:
        token_mint = mint or CONFIG.spl_currency_mint
        if token_mint is None:
            raise ValueError("game.spl_currency_mint must be configured")

        result = self._request(
            "getTokenAccountsByOwner",
            [
                owner,
                {"mint": token_mint},
                {"encoding": "jsonParsed"},
            ],
        )

        total = 0
        decimals = CONFIG.spl_currency_decimals
        for account in result.get("value", []):
            parsed = account["account"]["data"]["parsed"]
            amount = parsed["info"]["tokenAmount"]
            total += int(amount["amount"])
            decimals = int(amount["decimals"])

        return SplTokenBalance(
            owner=owner,
            mint=token_mint,
            amount=total,
            decimals=decimals,
            ui_amount=total / (10**decimals),
        )

    def get_owned_nft_mints(self, owner: str) -> list[str]:
        result = self._request(
            "getTokenAccountsByOwner",
            [
                owner,
                {"programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"},
                {"encoding": "jsonParsed"},
            ],
        )

        mints: list[str] = []
        for account in result.get("value", []):
            info = account["account"]["data"]["parsed"]["info"]
            amount = info["tokenAmount"]
            if amount["decimals"] == 0 and amount["amount"] == "1":
                mints.append(str(info["mint"]))
        return mints

    def get_metaplex_asset(self, mint: str) -> dict[str, Any] | None:
        try:
            return self._request("getAsset", [mint])
        except SolanaRpcError:
            return None

    def find_owned_nft_by_collection(
        self,
        owner: str,
        collection: str,
    ) -> OwnedNft | None:
        for mint in self.get_owned_nft_mints(owner):
            asset = self.get_metaplex_asset(mint)
            if asset is None:
                continue
            grouping = asset.get("grouping", [])
            if not any(
                group.get("group_key") == "collection"
                and group.get("group_value") == collection
                for group in grouping
            ):
                continue
            content = asset.get("content", {})
            metadata = content.get("metadata", {})
            return OwnedNft(
                mint=mint,
                owner=owner,
                collection=collection,
                name=metadata.get("name"),
                uri=content.get("json_uri"),
            )
        return None
