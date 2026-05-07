# SPDX-License-Identifier: GPL-3.0
from __future__ import annotations

from pathlib import Path

_COMPOUND_WORDS = (
    "adventurer",
    "beachcomber",
    "brownheroine",
    "childactor",
    "cooldude",
    "dragonrider",
    "enbyasian",
    "fashionista",
    "firefighter",
    "firenymph",
    "homemaker",
    "knightlord",
    "riverboatcaptain",
)

_COMPOUND_LABELS = {
    "brownheroine": "brown heroine",
    "childactor": "child actor",
    "cooldude": "cool dude",
    "dragonrider": "dragon rider",
    "enbyasian": "enby asian",
    "firefighter": "fire fighter",
    "firenymph": "fire nymph",
    "knightlord": "knight lord",
    "riverboatcaptain": "riverboat captain",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def available_character_skins() -> list[str]:
    """Return skins that have both overworld and combat sprite sheets."""
    root = _repo_root()
    overworld_dir = root / "mods" / "tuxemon" / "sprites"
    combat_dir = root / "mods" / "tuxemon" / "gfx" / "sprites" / "player"
    if not overworld_dir.exists() or not combat_dir.exists():
        return ["adventurer"]

    overworld = {path.stem for path in overworld_dir.glob("*.png")}
    combat = {path.stem for path in combat_dir.glob("*.png")}
    skins = sorted(overworld & combat)
    if "adventurer" in skins:
        skins.remove("adventurer")
        skins.insert(0, "adventurer")
    return skins or ["adventurer"]


def skin_label(skin: str) -> str:
    parts: list[str] = []
    for raw_part in skin.replace("-", "_").split("_"):
        parts.extend(_split_compound(raw_part))
    return " ".join(parts).upper()


def _split_compound(value: str) -> list[str]:
    if not value:
        return []
    if value in _COMPOUND_LABELS:
        return _COMPOUND_LABELS[value].split()
    for word in _COMPOUND_WORDS:
        if value.startswith(word) and value != word:
            suffix = value[len(word) :]
            label = _COMPOUND_LABELS.get(word, word)
            return label.split() + _split_compound(suffix)
    return [value]
