# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

import pygame
from pygame import SRCALPHA
from pygame.draw import rect as draw_rect
from pygame.font import Font
from pygame.surface import Surface

from tuxemon.platform.const import buttons, events
from tuxemon.state.state import State
from tuxemon.ui.text import draw_text

if TYPE_CHECKING:
    from tuxemon.base_client import BaseClient
    from tuxemon.platform.events import PlayerInput


class ChatInputState(State):
    name: ClassVar[str] = "ChatInputState"
    transparent = True

    def __init__(self, client: BaseClient, **kwargs: Any) -> None:
        super().__init__(client=client, **kwargs)
        self.text = ""
        self.char_limit = 120
        self.font = Font(None, self.scale_int(18))
        self.small_font = Font(None, self.scale_int(18))

    def draw(self, surface: Surface) -> None:
        overlay = Surface(surface.get_size(), SRCALPHA)
        box_width = min(self.scale_int(620), surface.get_width() - self.scale_int(48))
        box_height = self.scale_int(112)
        box_x = (surface.get_width() - box_width) // 2
        box_y = surface.get_height() - box_height - self.scale_int(36)
        box = pygame.Rect(box_x, box_y, box_width, box_height)
        draw_rect(overlay, (12, 16, 24, 230), box, border_radius=self.scale_int(8))
        draw_rect(overlay, (235, 239, 245, 255), box, width=2, border_radius=self.scale_int(8))
        draw_text(
            overlay,
            "Say",
            (box.x + self.scale_int(12), box.y + self.scale_int(8), box.width, self.scale_int(22)),
            scaling=self.client.context.scaling,
            font=self.small_font,
            font_color=(180, 190, 205),
        )
        text_rect = pygame.Rect(
            box.x + self.scale_int(18),
            box.y + self.scale_int(38),
            box.width - self.scale_int(36),
            box.height - self.scale_int(50),
        )
        line_height = max(self.font.get_linesize(), self.scale_int(18))
        max_lines = max(1, text_rect.height // line_height)
        wrapped = _wrap_text_to_width(
            self.text or "_",
            self.font,
            text_rect.width,
        )[-max_lines:]
        for index, line in enumerate(wrapped):
            draw_text(
                overlay,
                line,
                (
                    text_rect.x,
                    text_rect.y + (index * line_height),
                    text_rect.width,
                    line_height,
                ),
                scaling=self.client.context.scaling,
                font=self.font,
                font_color=(255, 255, 255),
            )
        surface.blit(overlay, (0, 0))

    def process_event(self, event: PlayerInput) -> PlayerInput | None:
        if event.pressed and event.button == buttons.A:
            self._send()
            return None
        if event.pressed and event.button == buttons.BACK:
            self.client.pop_state(self)
            return None
        if event.pressed and event.button == events.BACKSPACE:
            self.text = self.text[:-1]
            return None
        if event.pressed and event.button == events.UNICODE:
            self._append(str(event.value))
            return None
        return None

    def _append(self, char: str) -> None:
        if char == "\x16" or not char:
            return
        if char in "\r\n\t":
            return
        if not char.isprintable():
            return
        if len(self.text) >= self.char_limit:
            return
        self.text += char

    def _send(self) -> None:
        message = self.text.strip()
        if message and self.client.network_manager.client:
            self.client.network_manager.client.send_chat(message)
        self.client.pop_state(self)


def _wrap_text_to_width(text: str, font: Font, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        parts = _split_word_to_width(word, font, max_width)
        for part in parts:
            candidate = part if not current else f"{current} {part}"
            if font.size(candidate)[0] <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = part
        if word == "":
            candidate = f"{current} "
            if font.size(candidate)[0] <= max_width:
                current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _split_word_to_width(word: str, font: Font, max_width: int) -> list[str]:
    if font.size(word)[0] <= max_width:
        return [word]
    parts: list[str] = []
    current = ""
    for char in word:
        candidate = current + char
        if current and font.size(candidate)[0] > max_width:
            parts.append(current)
            current = char
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts
