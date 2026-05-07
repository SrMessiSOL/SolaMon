# SPDX-License-Identifier: GPL-3.0
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

from tuxemon.chain.character import CharacterProfile
from tuxemon.chain.fees import require_player_sol_for_chain
from tuxemon.chain.submitter import (
    ASSET_REGISTRY_DIR,
    require_remote_authority_for_distribution,
)
from tuxemon.chain.wallet import UnlockedWallet

logger = logging.getLogger(__name__)


def burn_asset_devnet(
    wallet: UnlockedWallet,
    character: CharacterProfile,
    *,
    kind: str,
    instance_id: str,
) -> dict[str, Any] | None:
    registry_path = ASSET_REGISTRY_DIR / f"{character.owner}.json"
    if not registry_path.exists():
        logger.warning("Skipping burn, missing asset registry: %s", registry_path)
        return None
    env = os.environ.copy()
    env["SOLAMON_SECRET_KEY_BASE64"] = base64.b64encode(
        wallet.solana_secret_key()
    ).decode("ascii")
    require_remote_authority_for_distribution(env)
    result = subprocess.run(
        [
            "node",
            "burn-asset.mjs",
            registry_path.resolve().as_posix(),
            character.owner,
            kind,
            instance_id,
            character.character_mint,
        ],
        cwd=Path(__file__).resolve().parents[2] / "tools" / "solana",
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.error("Failed to burn %s NFT %s: %s", kind, instance_id, result.stderr)
        return None
    return json.loads(result.stdout)


def burn_asset_for_session(session: object, *, kind: str, instance_id: str) -> None:
    client = session.client
    if not client.config.chain_enabled:
        return
    chain_session = client.chain_session
    if not chain_session.wallet or not chain_session.character:
        return
    if not require_player_sol_for_chain(session, f"burn {kind} {instance_id}"):
        return
    burn_asset_devnet(
        chain_session.wallet,
        chain_session.character,
        kind=kind,
        instance_id=instance_id,
    )
