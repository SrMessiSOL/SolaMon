from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tuxemon.chain.rpc import SolanaRpcClient
from tuxemon.user_config import CONFIG


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read the configured Solamon SPL balance for a wallet."
    )
    parser.add_argument("owner", help="Owner wallet public key")
    parser.add_argument("--mint", default=CONFIG.spl_currency_mint)
    parser.add_argument("--rpc", default=CONFIG.solana_rpc_url)
    args = parser.parse_args()

    balance = SolanaRpcClient(args.rpc).get_spl_token_balance(
        args.owner,
        args.mint,
    )
    print(
        f"{balance.owner} has {balance.ui_amount:g} "
        f"({balance.amount} raw) of {balance.mint}"
    )


if __name__ == "__main__":
    main()
