# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, ClassVar

import pygame
from pygame import SRCALPHA
from pygame.font import Font
from pygame.rect import Rect
from pygame.surface import Surface
from pygame_menu.menu import Menu

from tuxemon.chain.character import (
    create_character_profile,
    discover_character_profile,
    load_character_profile,
)
from tuxemon.chain.loading import show_blockchain_loading
from tuxemon.chain.skins import available_character_skins, skin_label
from tuxemon.chain.slots import chain_slot_is_loadable
from tuxemon.chain.session import configured_wallet_path
from tuxemon.chain.wallet_info import copy_to_clipboard
from tuxemon.chain.wallet import (
    WalletExistsError,
    WalletUnlockError,
    create_wallet,
    unlock_wallet,
)
from tuxemon.graphics import load_and_scale
from tuxemon.menu.menu import PygameMenuState
from tuxemon.menu.transitions import PopInClamped
from tuxemon.platform.const import buttons
from tuxemon.state.state import State
from tuxemon.ui.text import draw_text
from tuxemon.ui.text_alignment import HorizontalAlignment, VerticalAlignment
from tuxemon.tools import open_dialog

if TYPE_CHECKING:
    from tuxemon.base_client import BaseClient
    from tuxemon.platform.events import PlayerInput


class SolamonCharacterState(PygameMenuState):
    """Create the player character NFT anchor before entering the world."""

    name: ClassVar[str] = "SolamonCharacterState"
    shrink_to_items = True

    def __init__(
        self,
        client: BaseClient,
        on_ready: Callable[[], None],
        allow_existing_character: bool = True,
        **kwargs: Any,
    ) -> None:
        self.on_ready = on_ready
        self.allow_existing_character = allow_existing_character
        self.pending_name: str | None = None
        super().__init__(client=client, transition=PopInClamped(), **kwargs)
        self.add_menu_items(self.menu)

    def add_menu_items(self, menu: Menu) -> None:
        existing = self._existing_minted_character()
        if existing is not None:
            if self.allow_existing_character:
                menu.add.button("USE CHARACTER NFT", self.use_existing_character)
            else:
                menu.add.button("PLAYER NFT EXISTS", self.block_existing_character)
        else:
            menu.add.button("CREATE CHARACTER NFT", self.ask_name)
        menu.add.button("BACK", self.client.pop_state)

    def block_existing_character(self) -> None:
        open_dialog(
            self.client,
            [
                "This wallet already owns a player NFT.",
                "Load that character instead of starting a new game.",
            ],
            dialog_speed="max",
        )

    def _existing_minted_character(self):
        owner = self.client.chain_session.public_key
        if not owner:
            return None
        profile = load_character_profile(owner)
        if profile and profile.character_mint_status == "minted-devnet":
            return profile
        show_blockchain_loading(self.client)
        profile = discover_character_profile(owner)
        if profile and profile.character_mint_status == "minted-devnet":
            return profile
        return None

    def use_existing_character(self) -> None:
        profile = self._existing_minted_character()
        if profile is None:
            open_dialog(
                self.client,
                ["No minted character NFT found for this wallet."],
                dialog_speed="max",
            )
            return
        self.client.chain_session.set_character(profile)
        self.client.pop_state(self)
        if chain_slot_is_loadable(1, profile):
            self.client.event_engine.execute_action("load_game", [1, True], True)
            return
        self.on_ready()

    def ask_name(self) -> None:
        existing = self._existing_minted_character()
        if existing is not None:
            if self.allow_existing_character:
                self.use_existing_character()
            else:
                self.block_existing_character()
            return

        if self.client.chain_session.wallet is None:
            wallet_path = configured_wallet_path()
            if wallet_path.exists():
                self.client.push_state(
                    "InputMenu",
                    prompt="Wallet password",
                    callback=self.unlock_then_ask_name,
                    char_limit=64,
                )
            else:
                self.client.push_state(
                    "InputMenu",
                    prompt="Create wallet password",
                    callback=self.confirm_wallet_password,
                    char_limit=64,
                )
            return

        self.client.push_state(
            "InputMenu",
            prompt="Character name",
            callback=self.ask_skin,
            char_limit=24,
        )

    def ask_skin(self, name: str) -> None:
        self.pending_name = name.strip() or "Player"
        self.client.push_state(
            "SolamonSkinState",
            on_select=self.create,
        )

    def confirm_wallet_password(self, password: str) -> None:
        self.client.push_state(
            "InputMenu",
            prompt="Confirm wallet password",
            callback=lambda confirmation: self.create_wallet_then_ask_name(
                password,
                confirmation,
            ),
            char_limit=64,
        )

    def create_wallet_then_ask_name(self, password: str, confirmation: str) -> None:
        if password != confirmation:
            open_dialog(
                self.client,
                ["Wallet passwords do not match."],
                dialog_speed="max",
            )
            return
        try:
            create_wallet(password, configured_wallet_path())
            wallet = unlock_wallet(password, configured_wallet_path())
        except WalletExistsError:
            open_dialog(self.client, ["Wallet already exists."], dialog_speed="max")
            return
        except WalletUnlockError:
            open_dialog(
                self.client,
                ["Wallet was created but could not be unlocked."],
                dialog_speed="max",
            )
            return
        self.client.chain_session.set_wallet(wallet)
        self.ask_name()

    def unlock_then_ask_name(self, password: str) -> None:
        try:
            wallet = unlock_wallet(password, configured_wallet_path())
        except WalletUnlockError:
            open_dialog(self.client, ["Invalid wallet password."], dialog_speed="max")
            return
        self.client.chain_session.set_wallet(wallet)
        self.ask_name()

    def create(self, avatar_slug: str) -> None:
        wallet = self.client.chain_session.wallet
        if wallet is None:
            open_dialog(
                self.client,
                ["Unlock a wallet before creating a character."],
                dialog_speed="max",
            )
            return

        existing = self._existing_minted_character()
        if existing is not None:
            if self.allow_existing_character:
                self.use_existing_character()
            else:
                self.block_existing_character()
            return

        name = self.pending_name or "Player"
        try:
            show_blockchain_loading(self.client)
            profile = create_character_profile(
                wallet,
                name,
                avatar_slug=avatar_slug,
            )
        except RuntimeError as exc:
            message = str(exc)
            if "Not enough SOL" in message:
                try:
                    copy_to_clipboard(wallet.public_key)
                    copied = "Address copied to clipboard."
                except RuntimeError:
                    copied = "Copy failed. Use the address shown here."
                open_dialog(
                    self.client,
                    [
                        "Deposit SOL before creating the character NFT.",
                        wallet.public_key,
                        copied,
                    ],
                    dialog_speed="max",
                )
                return
            open_dialog(
                self.client,
                ["Character NFT creation failed.", message[:180]],
                dialog_speed="max",
            )
            return
        self.client.chain_session.set_character(profile)
        self.client.pop_state(self)
        self.on_ready()


class SolamonSkinState(State):
    """Pick the character skin before minting the player NFT."""

    name: ClassVar[str] = "SolamonSkinState"

    def __init__(
        self,
        client: BaseClient,
        on_select: Callable[[str], None],
        **kwargs: Any,
    ) -> None:
        super().__init__(client=client, **kwargs)
        self.on_select = on_select
        self.skins = available_character_skins()
        self.selected = 0
        self.page = 0
        self.columns = 4
        self.rows = 2
        self.page_size = self.columns * self.rows
        self.title_font = Font(None, self.scale_int(16))
        self.label_font = Font(None, self.scale_int(7))
        self.selected_font = Font(None, self.scale_int(12))
        self.help_font = Font(None, self.scale_int(7))
        self._preview_cache: dict[str, Surface] = {}

    def draw(self, surface: Surface) -> None:
        width, height = surface.get_size()
        overlay = Surface((width, height), SRCALPHA)
        overlay.fill((8, 12, 20, 238))

        margin = self.scale_int(10)
        title_h = self.scale_int(18)
        selected_h = self.scale_int(17)
        footer_h = self.scale_int(13)
        grid_top = margin + title_h + selected_h + self.scale_int(5)
        grid_h = height - grid_top - footer_h - margin
        grid_w = width - (margin * 2)
        gap = self.scale_int(5)
        card_w = (grid_w - (gap * (self.columns - 1))) // self.columns
        card_h = (grid_h - (gap * (self.rows - 1))) // self.rows

        draw_text(
            overlay,
            "CHOOSE SKIN",
            (margin, margin, grid_w, title_h),
            scaling=self.client.context.scaling,
            font=self.title_font,
            font_color=(255, 255, 255),
            h_alignment=HorizontalAlignment.CENTER,
            v_alignment=VerticalAlignment.CENTER,
        )
        draw_text(
            overlay,
            skin_label(self.skins[self.selected]),
            (margin, margin + title_h, grid_w, selected_h),
            scaling=self.client.context.scaling,
            font=self.selected_font,
            font_color=(255, 221, 82),
            h_alignment=HorizontalAlignment.CENTER,
            v_alignment=VerticalAlignment.CENTER,
        )

        start = self.page * self.page_size
        visible = self.skins[start : start + self.page_size]
        for index, skin in enumerate(visible):
            absolute = start + index
            col = index % self.columns
            row = index // self.columns
            rect = Rect(
                margin + col * (card_w + gap),
                grid_top + row * (card_h + gap),
                card_w,
                card_h,
            )
            self._draw_skin_card(overlay, rect, skin, absolute == self.selected)

        page_count = max(1, (len(self.skins) + self.page_size - 1) // self.page_size)
        footer = (
            f"{self.selected + 1}/{len(self.skins)}  "
            f"PAGE {self.page + 1}/{page_count}  "
            "ARROWS  ENTER SELECT  ESC BACK"
        )
        draw_text(
            overlay,
            footer,
            (margin, height - margin - footer_h, grid_w, footer_h),
            scaling=self.client.context.scaling,
            font=self.help_font,
            font_color=(225, 232, 245),
            h_alignment=HorizontalAlignment.CENTER,
            v_alignment=VerticalAlignment.CENTER,
        )
        surface.blit(overlay, (0, 0))

    def _draw_skin_card(
        self,
        surface: Surface,
        rect: Rect,
        skin: str,
        selected: bool,
    ) -> None:
        border = (255, 221, 82) if selected else (120, 132, 160)
        fill = (27, 33, 48, 255) if selected else (16, 21, 32, 245)
        pygame.draw.rect(surface, fill, rect, border_radius=self.scale_int(4))
        pygame.draw.rect(
            surface,
            border,
            rect,
            width=self.scale_int(2 if selected else 1),
            border_radius=self.scale_int(4),
        )

        preview = self._preview(skin)
        sprite_rect = preview.get_rect()
        sprite_rect.centerx = rect.centerx
        sprite_rect.centery = rect.y + int(rect.height * 0.47)
        surface.blit(preview, sprite_rect)

        label_rect = Rect(
            rect.x + self.scale_int(4),
            rect.bottom - self.scale_int(13),
            rect.width - self.scale_int(8),
            self.scale_int(10),
        )
        draw_text(
            surface,
            _compact_skin_label(skin),
            label_rect,
            scaling=self.client.context.scaling,
            font=self.label_font,
            font_color=(255, 255, 255) if selected else (205, 214, 230),
            h_alignment=HorizontalAlignment.CENTER,
            v_alignment=VerticalAlignment.CENTER,
        )

    def _preview(self, skin: str) -> Surface:
        cached = self._preview_cache.get(skin)
        if cached is not None:
            return cached
        sheet = load_and_scale(f"sprites/{skin}.png")
        columns = 3
        rows = 4
        frame_w = sheet.get_width() // columns
        frame_h = sheet.get_height() // rows
        frame = sheet.subsurface(Rect(frame_w, 0, frame_w, frame_h)).copy()
        max_h = self.scale_int(34)
        if frame.get_height() > max_h:
            ratio = max_h / frame.get_height()
            frame = pygame.transform.scale(
                frame,
                (
                    max(1, int(frame.get_width() * ratio)),
                    max(1, int(frame.get_height() * ratio)),
                ),
            )
        self._preview_cache[skin] = frame
        return frame

    def process_event(self, event: PlayerInput) -> PlayerInput | None:
        if not event.pressed:
            return None
        if event.button == buttons.A:
            self.pick(self.skins[self.selected])
            return None
        if event.button == buttons.BACK:
            self.client.pop_state(self)
            return None
        if event.button == buttons.RIGHT:
            self._move(1)
            return None
        if event.button == buttons.LEFT:
            self._move(-1)
            return None
        if event.button == buttons.DOWN:
            self._move(self.columns)
            return None
        if event.button == buttons.UP:
            self._move(-self.columns)
            return None
        return None

    def _move(self, delta: int) -> None:
        self.selected = (self.selected + delta) % len(self.skins)
        self.page = self.selected // self.page_size

    def pick(self, skin: str) -> None:
        self.client.pop_state(self)
        self.on_select(skin)


def _compact_skin_label(skin: str) -> str:
    label = skin_label(skin)
    words = label.split()
    if len(label) <= 18:
        return label
    if len(words) > 1:
        first = words[0]
        rest = " ".join(words[1:])
        if len(first) <= 18 and len(rest) <= 18:
            return f"{first}\n{rest}"
    return label[:17] + "."
