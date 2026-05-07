# SPDX-License-Identifier: GPL-3.0
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tuxemon.chain.rpc import SolanaRpcClient
from tuxemon.chain.loading import show_blockchain_loading

if TYPE_CHECKING:
    from tuxemon.session import Session

logger = logging.getLogger(__name__)

LAMPORTS_PER_SOL = 1_000_000_000
MIN_CHAIN_ACTION_LAMPORTS = 50_000_000
MIN_CHAIN_TX_LAMPORTS = 5_000_000


def wallet_address_for_session(session: Session) -> str:
    chain_session = session.client.chain_session
    wallet = getattr(chain_session, "wallet", None)
    if wallet is not None:
        address = str(getattr(wallet, "public_key", "") or "")
        if address:
            return address
    return str(getattr(chain_session, "public_key", "") or "")


def player_sol_lamports(address: str) -> int:
    return SolanaRpcClient().get_sol_balance(address)


def player_sol_balance_ok(
    address: str,
    *,
    minimum_lamports: int = MIN_CHAIN_ACTION_LAMPORTS,
) -> tuple[bool, int]:
    balance = player_sol_lamports(address)
    return balance >= minimum_lamports, balance


def require_player_sol_for_chain(
    session: Session,
    reason: str,
    *,
    minimum_lamports: int = MIN_CHAIN_ACTION_LAMPORTS,
) -> bool:
    client = session.client
    if not client.config.chain_enabled:
        return True

    address = wallet_address_for_session(session)
    if not address:
        _block_until_funded(
            session,
            reason,
            "No player vault address is available.",
            address="",
            minimum_lamports=minimum_lamports,
        )
        return False

    effective_minimum = _minimum_for_reason(reason, minimum_lamports)
    try:
        show_blockchain_loading(client)
        ok, balance = player_sol_balance_ok(
            address,
            minimum_lamports=effective_minimum,
        )
    except Exception:
        logger.warning(
            "Unable to verify player SOL balance before %s.",
            reason,
            exc_info=True,
        )
        _block_until_funded(
            session,
            reason,
            "Unable to verify SOL balance on devnet.",
            address=address,
            minimum_lamports=minimum_lamports,
        )
        return False

    if not ok:
        logger.warning(
            "Blocked chain action %s: %s lamports available, %s required.",
            reason,
            balance,
            effective_minimum,
        )
        _block_until_funded(
            session,
            reason,
            f"Vault balance is {_format_sol(balance)} SOL.",
            address=address,
            minimum_lamports=MIN_CHAIN_ACTION_LAMPORTS,
        )
        return False

    release_chain_fee_block(session)
    return True


def block_if_player_sol_low_after_chain(session: Session, reason: str) -> None:
    client = session.client
    if not client.config.chain_enabled:
        return
    address = wallet_address_for_session(session)
    if not address:
        return
    try:
        balance = player_sol_lamports(address)
    except Exception:
        logger.warning(
            "Unable to verify player SOL balance after %s.",
            reason,
            exc_info=True,
        )
        return
    if balance >= MIN_CHAIN_ACTION_LAMPORTS:
        release_chain_fee_block(session)
        return
    _block_until_funded(
        session,
        reason,
        f"Vault balance is {_format_sol(balance)} SOL.",
        address=address,
        minimum_lamports=MIN_CHAIN_ACTION_LAMPORTS,
    )


def show_pending_chain_fee_block(session: Session) -> None:
    pending = getattr(session.client, "_solamon_pending_fee_block", None)
    if not pending:
        return
    setattr(session.client, "_solamon_pending_fee_block", None)
    _block_until_funded(
        session,
        str(pending.get("reason") or "chain transaction"),
        str(pending.get("detail") or "Vault balance is low."),
        address=str(pending.get("address") or ""),
        minimum_lamports=int(
            pending.get("minimum_lamports") or MIN_CHAIN_ACTION_LAMPORTS
        ),
        defer_in_battle=False,
    )


def release_chain_fee_block(session: Session) -> None:
    client = session.client
    setattr(client, "_solamon_chain_fee_blocked", False)
    setattr(client, "_solamon_pending_fee_block", None)
    try:
        client.event_engine.resume()
    except Exception:
        logger.debug("Unable to resume event engine after SOL fee block.", exc_info=True)
    try:
        client.movement_manager.unlock_controls(session.player)
    except Exception:
        logger.debug("Unable to unlock player after SOL fee block.", exc_info=True)


def _block_until_funded(
    session: Session,
    reason: str,
    detail: str,
    *,
    address: str,
    minimum_lamports: int,
    defer_in_battle: bool = True,
) -> None:
    client = session.client
    if defer_in_battle and _is_battle_active(session):
        setattr(
            client,
            "_solamon_pending_fee_block",
            {
                "reason": reason,
                "detail": detail,
                "address": address,
                "minimum_lamports": minimum_lamports,
            },
        )
        return

    setattr(client, "_solamon_chain_fee_blocked", True)

    try:
        client.event_engine.suspend()
    except Exception:
        logger.debug("Unable to suspend event engine for SOL fee block.", exc_info=True)

    try:
        client.movement_manager.stop_char(session.player)
        client.movement_manager.lock_controls(session.player)
    except Exception:
        logger.debug("Unable to lock player for SOL fee block.", exc_info=True)

    minimum = _format_sol(minimum_lamports)
    lines = [
        "Not enough SOL to keep playing on-chain.",
        detail,
        f"Deposit at least {minimum} SOL to the player vault.",
    ]
    if address:
        lines.append(address)
    lines.append("Then refresh balance to continue.")

    try:
        if "SolamonBalanceBlockedState" not in client.active_state_names:
            client.push_state(
                "SolamonBalanceBlockedState",
                reason=reason,
                detail=detail,
                address=address,
                minimum_lamports=minimum_lamports,
            )
    except Exception:
        logger.debug("Unable to show SOL fee block state for %s.", reason, exc_info=True)
        try:
            from tuxemon.tools import open_dialog

            open_dialog(client, lines, dialog_speed="max")
        except Exception:
            logger.debug(
                "Unable to show SOL fee block dialog for %s.",
                reason,
                exc_info=True,
            )


def _format_sol(lamports: int) -> str:
    sol = lamports / LAMPORTS_PER_SOL
    return f"{sol:.3f}".rstrip("0").rstrip(".")


def _minimum_for_reason(reason: str, requested: int) -> int:
    if _reason_is_battle_end(reason):
        return min(requested, MIN_CHAIN_TX_LAMPORTS)
    return requested


def _reason_is_battle_end(reason: str) -> bool:
    return (
        reason.startswith("wild battle")
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
