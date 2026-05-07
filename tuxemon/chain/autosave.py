# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import logging
import json
from pathlib import Path

import pygame

from tuxemon.constants import paths
from tuxemon.chain.character import update_character_save_anchor
from tuxemon.chain.compact import (
    build_compact_payload,
    compact_uri,
    parse_compact_uri,
    write_compact_payload_file,
)
from tuxemon.chain.nft_sync import sync_current_state_to_nfts
from tuxemon.chain.fees import require_player_sol_for_chain
from tuxemon.chain.fees import block_if_player_sol_low_after_chain
from tuxemon.chain.slots import projection_path_for_slot
from tuxemon.chain.slots import load_onchain_save_data
from tuxemon.chain.slots import read_onchain_player_state
from tuxemon.chain.submitter import (
    prepare_projection_assets_for_wallet_devnet,
    submit_compact_save_devnet,
    submit_currency_compact_transaction_and_save_devnet,
    submit_currency_transaction_and_save_devnet,
    submit_player_save_devnet,
)
from tuxemon.save_system.save_manager import SaveManager
from tuxemon.session import Session
from tuxemon.tools import open_dialog
from tuxemon.user_config import CONFIG

logger = logging.getLogger(__name__)


def auto_save_chain_state(session: Session, reason: str) -> bool:
    if not _can_chain_save(session, reason):
        return False
    reason = _battle_end_reason_with_pending_chain_action(session, reason)
    game_action = _game_action_for_reason(reason)
    if game_action is None:
        logger.warning(
            "Skipping Solamon chain save for untyped gameplay reason: %s.",
            reason,
        )
        return False
    if not require_player_sol_for_chain(session, reason):
        return False
    if _needs_full_baseline_save(reason):
        return _full_auto_save_chain_state(session, reason)
    slot = session.current_slot
    chain_session = session.client.chain_session
    base_save_hash = _base_save_hash(chain_session.character)
    if base_save_hash is None:
        return _full_auto_save_chain_state(session, reason)
    previous_submit = CONFIG.config_model.game.solana_submit_saves
    _show_chain_message(session, "Saving game...")
    try:
        CONFIG.config_model.game.solana_submit_saves = False
        save_data = SaveManager.save(session, slot)
        if save_data is None:
            raise RuntimeError(f"Unable to reload saved slot {slot}")
        if _compact_requires_full_baseline(
            chain_session.character, save_data, reason
        ):
            submit_player_save_devnet(
                chain_session.wallet,
                chain_session.character,
                projection_path_for_slot(slot),
                game_action=game_action,
            )
            _mark_character_anchor_from_projection(session, projection_path_for_slot(slot))
            _sync_nfts_after_chain_save(session, slot, reason)
            block_if_player_sol_low_after_chain(session, reason)
            logger.info(
                "Full auto-saved Solamon chain state for structural drift in %s.",
                reason,
            )
            _show_chain_message(session, "Game saved.")
            _clear_pending_battle_chain_action(session, reason)
            return True
        projection_path = projection_path_for_slot(slot)
        projection_snapshot = _read_text_snapshot(projection_path)
        compact_path = _write_compact_for_slot(slot, save_data, base_save_hash, reason)
        asset_result = _prepare_missing_assets_for_compact(
            session, save_data, reason, game_action
        )
        try:
            result = submit_compact_save_devnet(
                chain_session.wallet,
                chain_session.character,
                projection_path,
                compact_path,
                game_action=game_action,
            )
        except Exception as exc:
            if _is_oversized_compact_error(exc):
                logger.warning(
                    "Compact Solamon save for %s was too large; falling back to full save.",
                    reason,
                )
                result = submit_player_save_devnet(
                    chain_session.wallet,
                    chain_session.character,
                    projection_path,
                    game_action=game_action,
                )
                _log_chain_result(
                    "full-fallback",
                    reason,
                    game_action,
                    result,
                    result.get("assets") if isinstance(result.get("assets"), dict) else asset_result,
                )
                _mark_character_anchor_from_projection(session, projection_path)
                _sync_nfts_after_chain_save(session, slot, reason)
                block_if_player_sol_low_after_chain(session, reason)
                logger.info("Full fallback saved Solamon chain state for %s.", reason)
                _show_chain_message(session, "Game saved.")
                _clear_pending_battle_chain_action(session, reason)
                return True
            _restore_text_snapshot(projection_path, projection_snapshot)
            raise
        _log_chain_result("compact", reason, game_action, result, asset_result)
        _mark_projection_compact(
            projection_path_for_slot(slot),
            result["compactHash"],
            base_save_hash,
            slot,
        )
        _mark_character_anchor(
            session,
            result["compactHash"],
            compact_uri(slot, base_save_hash),
        )
        _sync_nfts_after_chain_save(session, slot, reason)
        block_if_player_sol_low_after_chain(session, reason)
        logger.info("Auto-saved Solamon chain state for %s.", reason)
        _show_chain_message(session, "Game saved.")
        _clear_pending_battle_chain_action(session, reason)
        return True
    except Exception:
        logger.error(
            "Failed to auto-save Solamon chain state for %s.",
            reason,
            exc_info=True,
        )
        _show_chain_message(session, "Save failed.")
        return False
    finally:
        CONFIG.config_model.game.solana_submit_saves = previous_submit


def auto_save_chain_currency_transfer(
    session: Session,
    reason: str,
    *,
    direction: str,
    amount: int,
) -> bool:
    if amount <= 0:
        return auto_save_chain_state(session, reason)
    if not _can_chain_save(session, reason):
        return False
    game_action = _game_action_for_reason(reason, direction=direction)
    if game_action is None:
        logger.warning(
            "Skipping Solamon currency save for untyped gameplay reason: %s.",
            reason,
        )
        return False
    if not require_player_sol_for_chain(session, reason):
        return False
    slot = session.current_slot
    chain_session = session.client.chain_session
    base_save_hash = _base_save_hash(chain_session.character)
    if base_save_hash is None:
        return _full_auto_save_chain_currency_transfer(
            session,
            reason,
            direction=direction,
            amount=amount,
        )
    if _needs_full_baseline_save(reason):
        return _full_auto_save_chain_currency_transfer(
            session,
            reason,
            direction=direction,
            amount=amount,
        )
    previous_submit = CONFIG.config_model.game.solana_submit_saves
    _show_chain_message(session, "Saving game...")
    try:
        CONFIG.config_model.game.solana_submit_saves = False
        save_data = SaveManager.save(session, slot)
        if save_data is None:
            raise RuntimeError(f"Unable to reload saved slot {slot}")
        if _compact_requires_full_baseline(
            chain_session.character, save_data, reason
        ):
            submit_currency_transaction_and_save_devnet(
                chain_session.wallet,
                chain_session.character,
                projection_path_for_slot(slot),
                direction=direction,
                amount=amount,
                game_action=game_action,
            )
            _mark_character_anchor_from_projection(session, projection_path_for_slot(slot))
            _sync_nfts_after_chain_save(session, slot, reason)
            block_if_player_sol_low_after_chain(session, reason)
            logger.info(
                "Full auto-saved Solamon currency transfer for structural drift in %s: %s %s.",
                reason,
                direction,
                amount,
            )
            _show_chain_message(session, "Game saved.")
            return True
        projection_path = projection_path_for_slot(slot)
        projection_snapshot = _read_text_snapshot(projection_path)
        compact_path = _write_compact_for_slot(slot, save_data, base_save_hash, reason)
        asset_result = _prepare_missing_assets_for_compact(
            session, save_data, reason, game_action
        )
        try:
            result = submit_currency_compact_transaction_and_save_devnet(
                chain_session.wallet,
                chain_session.character,
                projection_path,
                compact_path,
                direction=direction,
                amount=amount,
                game_action=game_action,
            )
        except Exception as exc:
            if _is_oversized_compact_error(exc):
                logger.warning(
                    "Compact Solamon currency save for %s was too large; falling back to full save.",
                    reason,
                )
                result = submit_currency_transaction_and_save_devnet(
                    chain_session.wallet,
                    chain_session.character,
                    projection_path,
                    direction=direction,
                    amount=amount,
                    game_action=game_action,
                )
                _log_chain_result(
                    "currency-full-fallback",
                    reason,
                    game_action,
                    result,
                    result.get("assets") if isinstance(result.get("assets"), dict) else asset_result,
                )
                _mark_character_anchor_from_projection(session, projection_path)
                _sync_nfts_after_chain_save(session, slot, reason)
                block_if_player_sol_low_after_chain(session, reason)
                logger.info(
                    "Full fallback saved Solamon currency state for %s: %s %s.",
                    reason,
                    direction,
                    amount,
                )
                _show_chain_message(session, "Game saved.")
                return True
            _restore_text_snapshot(projection_path, projection_snapshot)
            raise
        _log_chain_result("currency-compact", reason, game_action, result, asset_result)
        _mark_projection_compact(
            projection_path_for_slot(slot),
            result["compactHash"],
            base_save_hash,
            slot,
        )
        _mark_character_anchor(
            session,
            result["compactHash"],
            compact_uri(slot, base_save_hash),
        )
        _sync_nfts_after_chain_save(session, slot, reason)
        block_if_player_sol_low_after_chain(session, reason)
        logger.info(
            "Auto-saved Solamon currency transfer for %s: %s %s.",
            reason,
            direction,
            amount,
        )
        _show_chain_message(session, "Game saved.")
        return True
    except Exception:
        logger.error(
            "Failed Solamon currency autosave for %s: %s %s.",
            reason,
            direction,
            amount,
            exc_info=True,
        )
        _show_chain_message(session, "Save failed.")
        return False
    finally:
        CONFIG.config_model.game.solana_submit_saves = previous_submit


def _game_action_for_reason(
    reason: str,
    *,
    direction: str | None = None,
) -> dict[str, object] | None:
    if reason.startswith("starter chosen "):
        return {
            "kind": "choose_starter",
            "primaryId": _reason_tail(reason, "starter chosen "),
            "amount": 1,
        }
    if reason.startswith("monster added "):
        return {
            "kind": "catch_solamon",
            "primaryId": _reason_tail(reason, "monster added "),
            "amount": 1,
        }
    if reason.startswith("monster caught "):
        monster_id, item_id = _parse_catch_reason(reason)
        return {
            "kind": "catch_solamon",
            "primaryId": monster_id,
            "secondaryId": item_id,
            "amount": 1,
        }
    if reason.startswith("monster released "):
        return {
            "kind": "release_solamon",
            "primaryId": _reason_tail(reason, "monster released "),
            "amount": 1,
        }
    if reason.startswith("item change "):
        item_id, quantity = _parse_item_change(reason)
        if quantity > 0:
            return {
                "kind": "grant_items",
                "primaryId": item_id,
                "amount": min(quantity, 99),
            }
    if reason == "state_item_bundle":
        return {
            "kind": "grant_items",
            "primaryId": "__state_bundle__",
            "amount": 99,
        }
    if reason.startswith("buy item "):
        item_id, quantity = _parse_item_quantity(reason, "buy item ")
        return {
            "kind": "buy_item",
            "primaryId": item_id,
            "amount": min(max(quantity, 1), 99),
        }
    if reason.startswith("sell item "):
        item_id, quantity = _parse_item_quantity(reason, "sell item ")
        return {
            "kind": "sell_item",
            "primaryId": item_id,
            "amount": min(max(quantity, 1), 99),
        }
    if (
        reason.startswith("paid healing party")
        or reason.startswith("paid healing ")
    ):
        return {
            "kind": "heal_party",
            "primaryId": "party",
            "amount": 1,
        }
    if reason.startswith("heal party") or reason.startswith("heal monster "):
        return {
            "kind": "battle_result",
            "primaryId": "heal_party",
            "amount": 1,
        }
    if (
        reason.startswith("wild battle")
        or reason.startswith("battle capture")
        or reason.startswith("battle loss")
        or reason.startswith("battle loss recovery")
        or reason.startswith("battle draw")
        or reason.startswith("battle reward")
    ):
        return {
            "kind": "battle_result",
            "primaryId": _battle_result_id(reason, direction),
            "amount": 1,
        }
    return None


def _reason_tail(reason: str, prefix: str) -> str:
    return reason.removeprefix(prefix).strip()[:32] or "unknown"


def _parse_item_change(reason: str) -> tuple[str, int]:
    return _parse_item_quantity(reason, "item change ")


def _parse_item_quantity(reason: str, prefix: str) -> tuple[str, int]:
    body = _reason_tail(reason, prefix)
    if " x" not in body:
        return body, 1
    item_id, quantity_text = body.rsplit(" x", 1)
    try:
        quantity = int(quantity_text)
    except ValueError:
        quantity = 1
    return item_id.strip()[:32] or "item", quantity


def _parse_catch_reason(reason: str) -> tuple[str, str]:
    body = _reason_tail(reason, "monster caught ")
    if " using " not in body:
        return body[:32] or "unknown", ""
    monster_id, item_id = body.split(" using ", 1)
    return monster_id.strip()[:32] or "unknown", item_id.strip()[:32]


def _battle_end_reason_with_pending_chain_action(
    session: Session,
    reason: str,
) -> str:
    if not _reason_is_battle_end(reason):
        return reason
    pending = getattr(
        session.client,
        "_solamon_pending_battle_chain_action",
        None,
    )
    if not isinstance(pending, dict):
        return reason
    if pending.get("kind") == "catch_solamon":
        monster_id = str(pending.get("primaryId") or "unknown")[:32]
        item_id = str(pending.get("secondaryId") or "")[:32]
        return f"monster caught {monster_id} using {item_id}".strip()
    return reason


def _clear_pending_battle_chain_action(session: Session, reason: str) -> None:
    if not reason.startswith("monster caught "):
        return
    if hasattr(session.client, "_solamon_pending_battle_chain_action"):
        delattr(session.client, "_solamon_pending_battle_chain_action")


def _battle_result_id(reason: str, direction: str | None) -> str:
    if direction == "reward" or reason.startswith("battle reward"):
        return "battle_reward"
    if reason.startswith("battle capture"):
        return "capture"
    if reason.startswith("wild battle"):
        return "wild_battle"
    if reason.startswith("battle loss"):
        return "battle_loss"
    if reason.startswith("battle loss recovery"):
        return "battle_loss"
    if reason.startswith("battle draw"):
        return "battle_draw"
    return "battle"


def _can_chain_save(session: Session, reason: str) -> bool:
    if not session.client.config.chain_enabled:
        return False
    if session.chain_autosave_suppressed:
        logger.info(
            "Skipping Solamon chain autosave for %s: load restore in progress.",
            reason,
        )
        return False
    if session.current_slot is None:
        logger.warning(
            "Skipping Solamon chain autosave for %s: no current slot.",
            reason,
        )
        return False
    chain_session = session.client.chain_session
    if not chain_session.wallet or not chain_session.character:
        logger.warning(
            "Skipping Solamon chain autosave for %s: wallet or character missing.",
            reason,
        )
        return False
    return True


def _needs_full_baseline_save(reason: str) -> bool:
    # Captures create a new monster NFT and spend the ball stack. The generic
    # compact payload often exceeds Solana packet limits here, so go straight
    # to the full save path until catch has a dedicated compact instruction.
    return reason.startswith("monster caught ")


def _full_auto_save_chain_state(session: Session, reason: str) -> bool:
    reason = _battle_end_reason_with_pending_chain_action(session, reason)
    slot = session.current_slot
    chain_session = session.client.chain_session
    previous_submit = CONFIG.config_model.game.solana_submit_saves
    _show_chain_message(session, "Saving game...")
    try:
        CONFIG.config_model.game.solana_submit_saves = False
        SaveManager.save(session, slot)
        result = submit_player_save_devnet(
            chain_session.wallet,
            chain_session.character,
            projection_path_for_slot(slot),
            game_action=_game_action_for_reason(reason),
        )
        _log_chain_result("full", reason, _game_action_for_reason(reason), result, result.get("assets") if isinstance(result.get("assets"), dict) else None)
        _mark_character_anchor_from_projection(session, projection_path_for_slot(slot))
        _sync_nfts_after_chain_save(session, slot, reason)
        block_if_player_sol_low_after_chain(session, reason)
        logger.info("Full auto-saved Solamon chain state for %s.", reason)
        _show_chain_message(session, "Game saved.")
        _clear_pending_battle_chain_action(session, reason)
        return True
    except Exception:
        logger.error(
            "Failed to full auto-save Solamon chain state for %s.",
            reason,
            exc_info=True,
        )
        _show_chain_message(session, "Save failed.")
        return False
    finally:
        CONFIG.config_model.game.solana_submit_saves = previous_submit


def _full_auto_save_chain_currency_transfer(
    session: Session,
    reason: str,
    *,
    direction: str,
    amount: int,
) -> bool:
    slot = session.current_slot
    chain_session = session.client.chain_session
    previous_submit = CONFIG.config_model.game.solana_submit_saves
    _show_chain_message(session, "Saving game...")
    try:
        CONFIG.config_model.game.solana_submit_saves = False
        SaveManager.save(session, slot)
        result = submit_currency_transaction_and_save_devnet(
            chain_session.wallet,
            chain_session.character,
            projection_path_for_slot(slot),
            direction=direction,
            amount=amount,
            game_action=_game_action_for_reason(reason, direction=direction),
        )
        _log_chain_result(
            "currency-full",
            reason,
            _game_action_for_reason(reason, direction=direction),
            result,
            result.get("assets") if isinstance(result.get("assets"), dict) else None,
        )
        _mark_character_anchor_from_projection(session, projection_path_for_slot(slot))
        _sync_nfts_after_chain_save(session, slot, reason)
        block_if_player_sol_low_after_chain(session, reason)
        logger.info(
            "Full auto-saved Solamon currency transfer for %s: %s %s.",
            reason,
            direction,
            amount,
        )
        _show_chain_message(session, "Game saved.")
        return True
    except Exception:
        logger.error(
            "Failed full Solamon currency autosave for %s: %s %s.",
            reason,
            direction,
            amount,
            exc_info=True,
        )
        _show_chain_message(session, "Save failed.")
        return False
    finally:
        CONFIG.config_model.game.solana_submit_saves = previous_submit


def _base_save_hash(character: object) -> str | None:
    if character is None:
        return None
    onchain = read_onchain_player_state(character)
    if onchain is None:
        return None
    parsed = parse_compact_uri(str(onchain.get("saveUri", "")))
    if parsed is not None:
        return parsed[1]
    return str(onchain.get("saveHash") or "") or None


def _compact_requires_full_baseline(
    character: object,
    save_data: object,
    reason: str,
) -> bool:
    if character is None:
        return True
    onchain = load_onchain_save_data(character)
    if onchain is None:
        return True
    local_npc = getattr(save_data, "npc_state", None)
    onchain_npc = getattr(onchain, "npc_state", None)
    if local_npc is None or onchain_npc is None:
        return True
    return False


def _prepare_missing_assets_for_compact(
    session: Session,
    save_data: object,
    reason: str,
    game_action: dict[str, object] | None,
) -> dict[str, object] | None:
    chain_session = session.client.chain_session
    if (
        chain_session.wallet is None
        or chain_session.character is None
        or not _reason_can_create_asset(reason)
    ):
        return None
    result = prepare_projection_assets_for_wallet_devnet(
        chain_session.wallet,
        chain_session.character,
        projection_path_for_slot(session.current_slot),
        game_action=game_action,
    )
    minted = int(result.get("mintedCount", 0) or 0)
    if minted:
        logger.info(
            "Minted %s missing Solamon NFT asset(s) before compact save for %s.",
            minted,
            reason,
        )
    return result


def _read_text_snapshot(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _restore_text_snapshot(path: Path, snapshot: str | None) -> None:
    if snapshot is None:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(snapshot, encoding="utf-8")


def _is_oversized_compact_error(exc: Exception) -> bool:
    message = str(exc)
    return (
        "Transaction too large" in message
        or "Compressed compact save is" in message
        or "max is" in message and "compact" in message.lower()
    )


def _reason_can_create_asset(reason: str) -> bool:
    return (
        reason.startswith("starter chosen ")
        or reason.startswith("monster added ")
        or reason.startswith("monster caught ")
        or reason.startswith("item change ")
        or reason.startswith("buy item ")
    )


def _log_chain_result(
    mode: str,
    reason: str,
    game_action: dict[str, object] | None,
    result: dict[str, object],
    asset_result: dict[str, object] | None,
) -> None:
    signatures = [
        key
        for key in (
            "signature",
            "saveSignature",
            "currencySignature",
            "compactWriteSignature",
        )
        if result.get(key)
    ]
    logger.info(
        "Solamon tx result: mode=%s action=%s reason=%s minted_nfts=%s signatures=%s instruction_count=%s compact_bytes=%s",
        mode,
        (game_action or {}).get("kind"),
        reason,
        int((asset_result or {}).get("mintedCount", 0) or 0),
        len(signatures) or (1 if result.get("signature") else 0),
        result.get("instructionCount", "unknown"),
        result.get("compressedBytes", "unknown"),
    )


def _asset_instance_ids(assets: object) -> tuple[str, ...]:
    ids: list[str] = []
    for asset in assets or []:
        if isinstance(asset, dict):
            ids.append(str(asset.get("instance_id") or asset.get("slug") or ""))
        else:
            ids.append(str(getattr(asset, "instance_id", "") or getattr(asset, "slug", "")))
    return tuple(ids)


def _has_missing_item_asset(character: object, items: object) -> bool:
    owner = str(getattr(character, "owner", "") or "")
    if not owner:
        return True
    registry_path = paths.USER_GAME_SAVE_DIR / "chain" / "assets" / f"{owner}.json"
    if registry_path.exists():
        with registry_path.open("r", encoding="utf-8") as registry_file:
            registry = json.load(registry_file)
    else:
        registry = {"assets": {}}

    assets = registry.get("assets", {})
    for instance_id in _asset_instance_ids(items):
        key = f"{owner}:item:{instance_id}"
        if not assets.get(key, {}).get("mint"):
            return True
    return False


def _sync_nfts_after_chain_save(session: Session, slot: int, reason: str) -> None:
    kinds = _metadata_sync_kinds(session, reason)
    if kinds == set():
        logger.info("Skipped Solamon NFT metadata sync for %s.", reason)
        return

    _show_chain_message(session, "Syncing NFTs...")
    try:
        result = sync_current_state_to_nfts(
            session,
            slot,
            keepalive=lambda: _paint_chain_overlay_now(session, "Syncing NFTs..."),
            kinds=kinds,
        )
    except Exception:
        logger.warning(
            "Solamon NFT metadata sync did not finish for %s; chain save remains valid.",
            reason,
            exc_info=True,
        )
        _show_chain_message(session, "Game saved.")
        return
    changed = [
        asset
        for asset in result.get("synced", [])
        if asset.get("changed")
    ]
    logger.info(
        "Synced Solamon NFT metadata for %s: %s changed, %s skipped.",
        reason,
        len(changed),
        len(result.get("skipped", [])),
    )


def _metadata_sync_kinds(session: Session, reason: str) -> set[str] | None:
    if _reason_is_battle_end(reason):
        if getattr(session.client, "_solamon_pending_battle_nft_sync", False):
            setattr(session.client, "_solamon_pending_battle_nft_sync", False)
            return None
        return set()

    if _is_battle_active(session):
        if _reason_affects_items(reason) or _reason_is_monster_important(reason):
            setattr(session.client, "_solamon_pending_battle_nft_sync", True)
        return set()

    if _reason_is_monster_important(reason):
        return {"monster"}

    if _reason_affects_items(reason):
        return {"item"}

    return set()


def _reason_affects_items(reason: str) -> bool:
    if reason.startswith("item change "):
        _item_id, quantity = _parse_item_change(reason)
        return quantity > 0 or quantity == -1
    return (
        reason.startswith("item ")
        or reason.startswith("buy item")
        or reason.startswith("sell item")
    )


def _reason_is_monster_important(reason: str) -> bool:
    return (
        reason.startswith("monster added")
        or reason.startswith("monster caught")
        or reason.startswith("monster released")
        or reason.startswith("monster evolved")
        or reason.startswith("monster level")
    )


def _reason_is_battle_end(reason: str) -> bool:
    return (
        reason.startswith("wild battle")
        or reason.startswith("battle capture")
        or reason.startswith("battle loss")
        or reason.startswith("battle loss recovery")
        or reason.startswith("battle draw")
        or reason.startswith("battle reward")
    )


def _is_battle_active(session: Session) -> bool:
    combat_session = getattr(session.client, "combat_session", None)
    if combat_session is None:
        return False
    return bool(getattr(combat_session, "players", None))


def _write_compact_for_slot(
    slot: int,
    save_data: object,
    base_save_hash: str,
    reason: str,
):
    compact = build_compact_payload(
        save_data,
        base_save_hash,
        include_item_structure=_reason_needs_item_structure(reason),
        include_monster_structure=_reason_needs_monster_structure(reason),
    )
    compact_dir = projection_path_for_slot(slot).parent / "compact"
    compact_dir.mkdir(parents=True, exist_ok=True)
    compact_path = compact_dir / f"slot{slot}.compact.json"
    write_compact_payload_file(compact, compact_path.as_posix())
    return compact_path


def _reason_needs_item_structure(reason: str) -> bool:
    return (
        reason.startswith("starter chosen ")
        or reason.startswith("monster added ")
        or reason.startswith("monster caught ")
        or reason.startswith("item change ")
        or reason.startswith("buy item ")
        or reason.startswith("sell item ")
    )


def _reason_needs_monster_structure(reason: str) -> bool:
    return (
        reason.startswith("starter chosen ")
        or reason.startswith("monster added ")
        or reason.startswith("monster caught ")
        or reason.startswith("monster released ")
    )


def _mark_projection_compact(
    projection_path,
    compact_hash: str,
    base_save_hash: str,
    slot: int,
) -> None:
    with projection_path.open("r", encoding="utf-8") as projection_file:
        projection = json.load(projection_file)
    projection["compact_save_hash"] = compact_hash
    projection["base_save_hash"] = base_save_hash
    projection["onchain_save_uri"] = compact_uri(slot, base_save_hash)
    with projection_path.open("w", encoding="utf-8") as projection_file:
        json.dump(projection, projection_file, indent=2, sort_keys=True)


def _mark_character_anchor_from_projection(session: Session, projection_path) -> None:
    with projection_path.open("r", encoding="utf-8") as projection_file:
        projection = json.load(projection_file)
    _mark_character_anchor(
        session,
        str(projection["save_hash"]),
        projection_path.as_posix(),
    )


def _mark_character_anchor(session: Session, save_hash: str, save_uri: str) -> None:
    chain_session = session.client.chain_session
    character = chain_session.character
    if character is None:
        return
    updated = update_character_save_anchor(
        character,
        save_hash=save_hash,
        save_uri=save_uri,
    )
    chain_session.set_character(updated)


def _show_chain_message(session: Session, message: str) -> None:
    try:
        if message.endswith("..."):
            _paint_chain_overlay_now(session, message)
        else:
            open_dialog(session.client, [message], dialog_speed="max")
    except Exception:
        logger.debug("Unable to show chain save dialog: %s", message, exc_info=True)


def _paint_chain_overlay_now(session: Session, message: str) -> None:
    client = session.client
    try:
        draw = getattr(client, "draw", None)
        if callable(draw):
            draw()
        _refresh_active_battle_ui(client)
        screen = getattr(client, "screen", None)
        if screen is None:
            return
        rect = screen.get_rect()
        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 96))
        box = pygame.Rect(0, 0, 360, 76)
        box.center = rect.center
        pygame.draw.rect(overlay, (16, 21, 30, 235), box, border_radius=8)
        pygame.draw.rect(overlay, (235, 239, 245, 255), box, width=2, border_radius=8)
        font = pygame.font.Font(None, 34)
        text = font.render(message, True, (255, 255, 255))
        text_rect = text.get_rect(center=box.center)
        overlay.blit(text, text_rect)
        screen.blit(overlay, (0, 0))
        pygame.display.update()
        pygame.event.pump()
    except Exception:
        logger.debug("Unable to paint chain save overlay immediately.", exc_info=True)


def _refresh_active_battle_ui(client: object) -> None:
    states = list(getattr(client, "active_states", []) or [])
    for state in reversed(states):
        if getattr(state, "name", "") == "CombatState":
            refresh = getattr(state, "refresh_ui", None)
            if callable(refresh):
                refresh()
            return
