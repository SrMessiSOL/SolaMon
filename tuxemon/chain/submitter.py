# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from tuxemon.chain.character import CharacterProfile, character_profile_path
from tuxemon.chain.wallet import UnlockedWallet
from tuxemon.constants import paths

ASSET_REGISTRY_DIR = paths.USER_GAME_SAVE_DIR / "chain" / "assets"
NFT_SYNC_TIMEOUT_SECONDS = 25.0


def submit_player_save_devnet(
    wallet: UnlockedWallet,
    character: CharacterProfile,
    projection_path: Path,
    *,
    dry_run: bool = False,
    game_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Submit a character-bound save anchor transaction through the Node helper.

    The decrypted Solana keypair is passed only through the child process
    environment and is never written to disk by this wrapper.
    """
    env = os.environ.copy()
    env["SOLAMON_SECRET_KEY_BASE64"] = base64.b64encode(
        wallet.solana_secret_key()
    ).decode("ascii")
    _set_game_action_env(env, game_action)
    require_remote_authority_for_distribution(env)
    assets = prepare_projection_assets_devnet(character, projection_path, env=env)

    result = subprocess.run(
        [
            "node",
            "submit-player-save.mjs",
            character_profile_path(character.owner).resolve().as_posix(),
            projection_path.resolve().as_posix(),
            "dry-run" if dry_run else "send",
        ],
        cwd=_solana_tools_dir(),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    payload = json.loads(result.stdout)
    payload["assets"] = assets
    return payload


def submit_compact_save_devnet(
    wallet: UnlockedWallet,
    character: CharacterProfile,
    projection_path: Path,
    compact_path: Path,
    *,
    dry_run: bool = False,
    game_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["SOLAMON_SECRET_KEY_BASE64"] = base64.b64encode(
        wallet.solana_secret_key()
    ).decode("ascii")
    _set_game_action_env(env, game_action)
    require_remote_authority_for_distribution(env)

    result = subprocess.run(
        [
            "node",
            "submit-compact-save.mjs",
            character_profile_path(character.owner).resolve().as_posix(),
            projection_path.resolve().as_posix(),
            compact_path.resolve().as_posix(),
            "dry-run" if dry_run else "send",
        ],
        cwd=_solana_tools_dir(),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)


def prepare_projection_assets_devnet(
    character: CharacterProfile,
    projection_path: Path,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    ASSET_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    registry_path = ASSET_REGISTRY_DIR / f"{character.owner}.json"
    result = subprocess.run(
        [
            "node",
            "mint-projection-assets.mjs",
            projection_path.resolve().as_posix(),
            registry_path.resolve().as_posix(),
        ],
        cwd=_solana_tools_dir(),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)


def prepare_projection_assets_for_wallet_devnet(
    wallet: UnlockedWallet,
    character: CharacterProfile,
    projection_path: Path,
    *,
    game_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    env = os.environ.copy()
    env["SOLAMON_SECRET_KEY_BASE64"] = base64.b64encode(
        wallet.solana_secret_key()
    ).decode("ascii")
    _set_game_action_env(env, game_action)
    require_remote_authority_for_distribution(env)
    return prepare_projection_assets_devnet(character, projection_path, env=env)


def sync_nft_metadata_devnet(
    character: CharacterProfile,
    projection_path: Path,
    *,
    wallet: UnlockedWallet | None = None,
    dry_run: bool = False,
    keepalive: Callable[[], None] | None = None,
    kinds: set[str] | None = None,
) -> dict[str, Any]:
    ASSET_REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
    registry_path = ASSET_REGISTRY_DIR / f"{character.owner}.json"
    args = [
        "node",
        "sync-nft-metadata.mjs",
        projection_path.resolve().as_posix(),
        registry_path.resolve().as_posix(),
        "dry-run" if dry_run else "send",
        ",".join(sorted(kinds)) if kinds else "all",
    ]
    env = os.environ.copy()
    if wallet is not None:
        env["SOLAMON_SECRET_KEY_BASE64"] = base64.b64encode(
            wallet.solana_secret_key()
        ).decode("ascii")
    require_remote_authority_for_distribution(env)
    if keepalive is None:
        try:
            result = subprocess.run(
                args,
                cwd=_solana_tools_dir(),
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=NFT_SYNC_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("NFT metadata sync timed out.") from exc
    else:
        result = _run_with_keepalive(
            args,
            keepalive,
            env=env,
            timeout_seconds=NFT_SYNC_TIMEOUT_SECONDS,
        )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)


def _run_with_keepalive(
    args: list[str],
    keepalive: Callable[[], None],
    *,
    env: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[str]:
    process = subprocess.Popen(
        [
            *args,
        ],
        cwd=_solana_tools_dir(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    started_at = time.monotonic()
    while process.poll() is None:
        if (
            timeout_seconds is not None
            and time.monotonic() - started_at >= timeout_seconds
        ):
            process.kill()
            stdout, stderr = process.communicate()
            return subprocess.CompletedProcess(
                args=args,
                returncode=124,
                stdout=stdout,
                stderr=(stderr or "") + "\nNFT metadata sync timed out.",
            )
        keepalive()
        time.sleep(0.1)
    stdout, stderr = process.communicate()
    return subprocess.CompletedProcess(
        args=args,
        returncode=process.returncode,
        stdout=stdout,
        stderr=stderr,
    )


def submit_currency_transaction_and_save_devnet(
    wallet: UnlockedWallet,
    character: CharacterProfile,
    projection_path: Path,
    *,
    direction: str,
    amount: str | int | float,
    dry_run: bool = False,
    game_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Move SLMN through treasury and anchor the save in the same transaction.

    `direction="reward"` transfers from treasury to player.
    `direction="spend"` transfers from player to treasury.
    """
    if direction not in {"reward", "spend"}:
        raise ValueError("direction must be 'reward' or 'spend'")
    env = os.environ.copy()
    env["SOLAMON_SECRET_KEY_BASE64"] = base64.b64encode(
        wallet.solana_secret_key()
    ).decode("ascii")
    _set_game_action_env(env, game_action)
    require_remote_authority_for_distribution(env)
    assets = prepare_projection_assets_devnet(character, projection_path, env=env)

    result = subprocess.run(
        [
            "node",
            "currency-transaction-and-save.mjs",
            character_profile_path(character.owner).resolve().as_posix(),
            projection_path.resolve().as_posix(),
            direction,
            str(amount),
            "dry-run" if dry_run else "send",
        ],
        cwd=_solana_tools_dir(),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    payload = json.loads(result.stdout)
    payload["assets"] = assets
    return payload


def submit_currency_compact_transaction_and_save_devnet(
    wallet: UnlockedWallet,
    character: CharacterProfile,
    projection_path: Path,
    compact_path: Path,
    *,
    direction: str,
    amount: str | int | float,
    dry_run: bool = False,
    game_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if direction not in {"reward", "spend"}:
        raise ValueError("direction must be 'reward' or 'spend'")
    env = os.environ.copy()
    env["SOLAMON_SECRET_KEY_BASE64"] = base64.b64encode(
        wallet.solana_secret_key()
    ).decode("ascii")
    _set_game_action_env(env, game_action)
    require_remote_authority_for_distribution(env)

    result = subprocess.run(
        [
            "node",
            "currency-compact-transaction-and-save.mjs",
            character_profile_path(character.owner).resolve().as_posix(),
            projection_path.resolve().as_posix(),
            compact_path.resolve().as_posix(),
            direction,
            str(amount),
            "dry-run" if dry_run else "send",
        ],
        cwd=_solana_tools_dir(),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return json.loads(result.stdout)


def _solana_tools_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "tools" / "solana"


def _set_game_action_env(
    env: dict[str, str],
    game_action: dict[str, Any] | None,
) -> None:
    if game_action is None:
        env.pop("SOLAMON_GAME_ACTION", None)
        return
    env["SOLAMON_GAME_ACTION"] = json.dumps(game_action, separators=(",", ":"))


def require_remote_authority_for_distribution(env: dict[str, str]) -> None:
    if env.get("SOLAMON_REQUIRE_AUTHORITY") == "1" and not env.get(
        "SOLAMON_AUTHORITY_URL"
    ):
        raise RuntimeError(
            "SOLAMON_AUTHORITY_URL is required when SOLAMON_REQUIRE_AUTHORITY=1"
        )
