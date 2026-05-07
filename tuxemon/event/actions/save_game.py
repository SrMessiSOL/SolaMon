# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import final

import pygame

from tuxemon.chain.autosave import auto_save_chain_state
from tuxemon.chain.fees import require_player_sol_for_chain
from tuxemon.chain.nft_sync import sync_current_state_to_nfts
from tuxemon.chain.slots import read_onchain_player_state
from tuxemon.event.eventaction import EventAction
from tuxemon.locale.locale import T
from tuxemon.save_system.save_slots import resolve_save_index
from tuxemon.session import Session
from tuxemon.tools import open_dialog

logger = logging.getLogger(__name__)


@final
@dataclass
class SaveGameAction(EventAction):
    """
    Saves the game to a specific save slot.

    The `index` parameter refers to the UI slot index (0-2).
    Slot resolution is handled by `resolve_save_index()`, which converts
    the UI index (0-based) into a save slot number (1-based).

    Script usage:
        .. code-block::

            save_game <index>

    Script parameters:
        index: UI slot index (0-2). Must always be provided.
    """

    name = "save_game"
    index: int

    def start(self, session: Session) -> None:
        slot = resolve_save_index(self.index)
        if session.client.config.chain_enabled:
            slot = 1

        logger.info("Saving!")
        try:
            if session.client.config.chain_enabled:
                if not require_player_sol_for_chain(session, "manual NFT sync"):
                    return
                self._repair_missing_chain_anchor(session)
            self._paint_saving_overlay_now(session, "Syncing NFTs...")
            result = sync_current_state_to_nfts(
                session,
                slot,
                keepalive=lambda: self._paint_saving_overlay_now(
                    session,
                    "Syncing NFTs...",
                ),
            )
            synced = len(result.get("synced", []))
            open_dialog(
                session.client,
                [f"Synced {synced} NFT records."],
                dialog_speed="max",
            )
        except Exception as e:
            logger.error("Unable to sync NFT data!")
            logger.exception(e)
            message = str(e)
            if "timed out" in message.lower():
                open_dialog(
                    session.client,
                    ["NFT sync timed out. Game state remains on-chain."],
                    dialog_speed="max",
                )
                return
            open_dialog(
                session.client,
                [T.translate("save_failure")],
                dialog_speed="max",
            )

    def _repair_missing_chain_anchor(self, session: Session) -> None:
        character = session.client.chain_session.character
        if character is None:
            return
        onchain = read_onchain_player_state(character)
        if onchain and str(onchain.get("saveHash", "")).strip("0"):
            return
        if not session.player.monsters:
            return
        starter = session.player.monsters[0].slug
        self._paint_saving_overlay_now(session, "Saving game...")
        saved = auto_save_chain_state(session, f"starter chosen {starter}")
        if not saved:
            raise RuntimeError("Unable to anchor starter save on-chain.")

    def _paint_saving_overlay_now(self, session: Session, message: str) -> None:
        try:
            draw = getattr(session.client, "draw", None)
            if callable(draw):
                draw()
            screen = getattr(session.client, "screen", None)
            if screen is None:
                return
            rect = screen.get_rect()
            overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 96))
            box = pygame.Rect(0, 0, 360, 76)
            box.center = rect.center
            pygame.draw.rect(overlay, (16, 21, 30, 235), box, border_radius=8)
            pygame.draw.rect(
                overlay, (235, 239, 245, 255), box, width=2, border_radius=8
            )
            font = pygame.font.Font(None, 34)
            text = font.render(message, True, (255, 255, 255))
            overlay.blit(text, text.get_rect(center=box.center))
            screen.blit(overlay, (0, 0))
            pygame.display.update()
            pygame.event.pump()
        except Exception:
            logger.debug("Unable to paint save overlay immediately.", exc_info=True)
