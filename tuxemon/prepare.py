# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
"""This module initializes the display, pygame, translations, and databases."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

import pygame as pg

from tuxemon.platform.const.sizes import NATIVE_RESOLUTION
from tuxemon.platform.const.sizes import TILE_SIZE as NATIVE_TILE_SIZE
from tuxemon.scaling import (
    DefaultScaling,
    ScalingStrategy,
    make_default_scaling,
)
from tuxemon.user_config import CONFIG

logger = logging.getLogger(__name__)


@dataclass
class DisplayContext:
    screen: pg.Surface
    rect: pg.Rect
    resolution: tuple[int, int]
    tile_size: tuple[int, int]
    scale: int
    scaling: ScalingStrategy


_default_surface = pg.Surface((1, 1))
_default_rect = _default_surface.get_rect()

DISPLAY_CONTEXT: DisplayContext = DisplayContext(
    screen=_default_surface,
    rect=_default_rect,
    resolution=(1, 1),
    tile_size=(1, 1),
    scale=1,
    scaling=DefaultScaling(1),
)


DEV_TOOLS = CONFIG.dev_tools


def pygame_init() -> DisplayContext:
    """Initializes Pygame, display, translations, and databases."""
    global DISPLAY_CONTEXT

    _startup_trace("pygame_init: core_init start")
    core_init()
    _startup_trace("pygame_init: core_init done")

    logger.debug("pygame init")
    _startup_trace("pygame_init: pygame init start")
    pg.init()
    pg.display.set_caption(CONFIG.window_caption)
    _startup_trace(f"pygame_init: pygame init done driver={pg.display.get_driver()}")

    scaling = make_default_scaling(CONFIG, NATIVE_RESOLUTION)

    # Fullscreen flags
    fullscreen = pg.FULLSCREEN if CONFIG.fullscreen else 0

    from tuxemon.platform import platform

    if platform.is_android():
        fullscreen = pg.FULLSCREEN

    flags = fullscreen

    if CONFIG.vsync:
        pg.display.set_allow_screensaver()

    _startup_trace(
        f"pygame_init: set_mode start resolution={CONFIG.resolution} flags={flags} vsync={CONFIG.vsync}"
    )
    screen = pg.display.set_mode(CONFIG.resolution, flags, vsync=CONFIG.vsync)
    _startup_trace("pygame_init: set_mode done")
    rect = screen.get_rect()

    pg.mouse.set_visible(not CONFIG.controller.hide_mouse)

    DISPLAY_CONTEXT = DisplayContext(
        screen=screen,
        rect=rect,
        resolution=CONFIG.resolution,
        tile_size=scaling.scale_point(NATIVE_TILE_SIZE),
        scale=scaling._scale,
        scaling=scaling,
    )

    return DISPLAY_CONTEXT


def headless_init() -> DisplayContext:
    """Initializes game components for a headless environment."""
    global DISPLAY_CONTEXT

    logger.debug("headless init")

    os.environ["SDL_VIDEODRIVER"] = "dummy"

    core_init()

    pg.display.init()
    pg.font.init()

    screen = pg.Surface(CONFIG.resolution)
    rect = screen.get_rect()

    DISPLAY_CONTEXT = DisplayContext(
        screen=screen,
        rect=rect,
        resolution=CONFIG.resolution,
        tile_size=NATIVE_TILE_SIZE,
        scale=1,
        scaling=DefaultScaling(1),
    )

    return DISPLAY_CONTEXT


def core_init() -> None:
    from tuxemon.database.runtime import db
    from tuxemon.locale.locale import T

    _startup_trace("core_init: translations start")
    T.initialize_translations(recompile=CONFIG.recompile_translations)
    _startup_trace("core_init: translations done")
    _startup_trace("core_init: db.load start")
    db.load()
    _startup_trace("core_init: db.load done")
    logger.debug("Initializing core systems")


def _startup_trace(message: str) -> None:
    trace_path = os.environ.get("SOLAMON_STARTUP_TRACE")
    if not trace_path:
        return
    try:
        with Path(trace_path).open("a", encoding="utf-8") as handle:
            handle.write(f"{message}\n")
    except OSError:
        pass
