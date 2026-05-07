from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tuxemon.chain.wallet import create_wallet, import_solana_keypair, unlock_wallet


def read_password(prompt: str = "Wallet password: ") -> str:
    password = os.environ.get("SOLAMON_WALLET_PASSWORD")
    if password is not None:
        return password
    return getpass.getpass(prompt)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage a Solamon wallet.")
    parser.add_argument("command", choices=("create", "import-keypair", "show"))
    parser.add_argument("--path", type=Path, default=None)
    parser.add_argument("--keypair", type=Path, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    wallet_path = args.path

    if args.command == "create":
        password = read_password()
        if "SOLAMON_WALLET_PASSWORD" not in os.environ:
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                raise SystemExit("Passwords do not match.")

        wallet = (
            create_wallet(password, overwrite=args.overwrite)
            if wallet_path is None
            else create_wallet(password, wallet_path, overwrite=args.overwrite)
        )
        print(wallet.public_key)
        return

    if args.command == "import-keypair":
        if args.keypair is None:
            raise SystemExit("--keypair is required for import-keypair")
        password = read_password()
        if "SOLAMON_WALLET_PASSWORD" not in os.environ:
            confirm = getpass.getpass("Confirm password: ")
            if password != confirm:
                raise SystemExit("Passwords do not match.")
        wallet = (
            import_solana_keypair(
                args.keypair,
                password,
                overwrite=args.overwrite,
            )
            if wallet_path is None
            else import_solana_keypair(
                args.keypair,
                password,
                wallet_path,
                overwrite=args.overwrite,
            )
        )
        print(wallet.public_key)
        return

    password = read_password()
    wallet = (
        unlock_wallet(password)
        if wallet_path is None
        else unlock_wallet(password, wallet_path)
    )
    print(wallet.public_key)


if __name__ == "__main__":
    main()
