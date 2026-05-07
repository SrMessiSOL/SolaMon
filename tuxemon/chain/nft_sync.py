# SPDX-License-Identifier: GPL-3.0
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from tuxemon.chain.projection import write_chain_projection
from tuxemon.chain.submitter import sync_nft_metadata_devnet
from tuxemon.constants import paths
from tuxemon.save_system import save
from tuxemon.session import Session


def sync_current_state_to_nfts(
    session: Session,
    slot: int,
    *,
    keepalive: Callable[[], None] | None = None,
    kinds: set[str] | None = None,
) -> dict[str, Any]:
    chain_session = session.client.chain_session
    if chain_session.character is None:
        raise RuntimeError("No Solamon character loaded")

    save_data = save.get_save_data(session)
    sync_dir = paths.USER_GAME_SAVE_DIR / "chain" / "sync"
    sync_dir.mkdir(parents=True, exist_ok=True)
    sync_save_path = sync_dir / f"slot{slot}.sync.json"
    projection_path, _save_hash = write_chain_projection(
        save_data,
        sync_save_path,
        chain_session.character,
    )
    return sync_nft_metadata_devnet(
        chain_session.character,
        projection_path,
        wallet=chain_session.wallet,
        keepalive=keepalive,
        kinds=kinds,
    )
