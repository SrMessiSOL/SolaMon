# SPDX-License-Identifier: GPL-3.0
from __future__ import annotations

import logging

import pygame

logger = logging.getLogger(__name__)


def show_blockchain_loading(client: object, message: str = "Loading blockchain...") -> None:
    """Paint an immediate modal overlay before a blocking chain read."""
    try:
        draw = getattr(client, "draw", None)
        if callable(draw):
            draw()
        screen = getattr(client, "screen", None)
        if screen is None:
            return
        rect = screen.get_rect()
        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 96))
        box = pygame.Rect(0, 0, 420, 76)
        box.center = rect.center
        pygame.draw.rect(overlay, (16, 21, 30, 235), box, border_radius=8)
        pygame.draw.rect(overlay, (235, 239, 245, 255), box, width=2, border_radius=8)
        font = pygame.font.Font(None, 34)
        text = font.render(message, True, (255, 255, 255))
        overlay.blit(text, text.get_rect(center=box.center))
        screen.blit(overlay, (0, 0))
        pygame.display.update()
        pygame.event.pump()
    except Exception:
        logger.debug("Unable to paint blockchain loading overlay.", exc_info=True)
