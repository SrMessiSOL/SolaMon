# SPDX-License-Identifier: GPL-3.0
from __future__ import annotations

import json
import logging
import base64
import os
import subprocess
from pathlib import Path
from typing import Any

from tuxemon.chain.wallet import UnlockedWallet
from tuxemon.constants import paths
from tuxemon.tools import open_dialog

logger = logging.getLogger(__name__)


def open_wallet_info_dialog(client: object) -> None:
    chain_session = client.chain_session
    owner = chain_session.public_key
    if not owner:
        open_dialog(client, ["No Solamon wallet is unlocked."], dialog_speed="max")
        return
    try:
        info = read_wallet_info(owner)
    except Exception:
        logger.error("Unable to read Solamon wallet info.", exc_info=True)
        open_dialog(client, ["Unable to read wallet info."], dialog_speed="max")
        return

    open_dialog(
        client,
        [
            f"Wallet: {info['owner']}",
            f"SOL: {info['sol']:.6f}",
            f"{info['tokenSymbol']}: {info['tokenUiAmount']}",
            "Deposit: send SOL to this wallet address.",
            "Withdraw support is next.",
        ],
        dialog_speed="max",
    )


def read_wallet_info(owner: str) -> dict[str, object]:
    result = subprocess.run(
        ["node", "wallet-info.mjs", owner],
        cwd=Path(__file__).resolve().parents[2] / "tools" / "solana",
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)


def copy_to_clipboard(text: str) -> None:
    try:
        subprocess.run(
            ["clip"],
            input=text,
            text=True,
            check=True,
            capture_output=True,
        )
        return
    except Exception:
        logger.debug("Windows clip.exe clipboard copy failed.", exc_info=True)

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Set-Clipboard"],
            input=text,
            text=True,
            check=True,
            capture_output=True,
        )
    except Exception as exc:
        raise RuntimeError("Unable to copy to clipboard.") from exc


def export_keypair(wallet: UnlockedWallet) -> Path:
    export_path = paths.USER_GAME_SAVE_DIR / "solamon_keypair_export.json"
    secret = list(wallet.solana_secret_key())
    with export_path.open("w", encoding="utf-8") as export_file:
        json.dump(secret, export_file)
    return export_path


def withdraw_sol(wallet: UnlockedWallet, destination: str, amount_sol: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["SOLAMON_SECRET_KEY_BASE64"] = base64.b64encode(
        wallet.solana_secret_key()
    ).decode("ascii")
    result = subprocess.run(
        ["node", "transfer-sol.mjs", destination, amount_sol],
        cwd=Path(__file__).resolve().parents[2] / "tools" / "solana",
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)
