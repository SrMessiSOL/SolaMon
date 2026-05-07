# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from tuxemon.constants import paths
from tuxemon.save_system.save_state import SaveData

SAVE_BLOB_DIR = paths.USER_GAME_SAVE_DIR / "chain" / "blobs"


def canonical_save_payload(save_data: SaveData) -> dict[str, Any]:
    payload = save_data.model_dump(mode="json")
    payload.pop("screenshot", None)
    payload.pop("screenshot_width", None)
    payload.pop("screenshot_height", None)
    return payload


def hash_payload(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_save_blob(save_data: SaveData) -> tuple[Path, str]:
    payload = canonical_save_payload(save_data)
    save_hash = hash_payload(payload)
    SAVE_BLOB_DIR.mkdir(parents=True, exist_ok=True)
    blob_path = SAVE_BLOB_DIR / f"{save_hash}.json"
    with blob_path.open("w", encoding="utf-8") as blob_file:
        json.dump(payload, blob_file, indent=2, sort_keys=True)
    return blob_path, save_hash
