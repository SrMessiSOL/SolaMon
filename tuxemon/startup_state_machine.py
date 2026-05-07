# SPDX-License-Identifier: GPL-3.0
# Copyright (c) 2014-2026 William Edwards <shadowapex@gmail.com>, Benjamin Bean <superman2k5@gmail.com>
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, ClassVar

from tuxemon.database.runtime import db
from tuxemon.launcher import GameLauncher
from tuxemon.chain.session import configured_wallet_path
from tuxemon.chain.slots import chain_slot_is_loadable
from tuxemon.chain.wallet import (
    WalletExistsError,
    WalletUnlockError,
    create_wallet,
    unlock_wallet,
)
from tuxemon.session import local_session
from tuxemon.startup_rules import StartupRule, load_mod_startup_rules
from tuxemon.tools import open_dialog

if TYPE_CHECKING:
    from tuxemon.client import LocalPygameClient
    from tuxemon.config import TuxemonConfig

logger = logging.getLogger(__name__)


class RuleBackground(StartupRule):
    name: ClassVar[str] = "RuleBackground"

    def __init__(self, client: LocalPygameClient):
        self.client = client

    def should_apply(self) -> bool:
        return True

    def apply(self) -> None:
        self.client.push_state("BackgroundState")


class RuleIntro(StartupRule):
    name: ClassVar[str] = "RuleIntro"

    def __init__(self, client: LocalPygameClient, config: TuxemonConfig):
        self.client = client
        self.config = config

    def should_apply(self) -> bool:
        return not self.config.skip_titlescreen

    def apply(self) -> None:
        self.client.push_state("IntroState")


class RuleLoadSlot(StartupRule):
    name: ClassVar[str] = "RuleLoadSlot"

    def __init__(
        self, client: LocalPygameClient, load_slot: int | None = None
    ):
        self.client = client
        self.load_slot = load_slot

    def should_apply(self) -> bool:
        return self.load_slot is not None

    def apply(self) -> None:
        if self.client.config.chain_enabled:
            def _load() -> None:
                self.client.push_state(
                    "LoadMenuState", load_slot=self.load_slot
                )

            def _require_character() -> None:
                if self.client.chain_session.has_character:
                    _load()
                else:
                    self.client.push_state(
                        "SolamonCharacterState",
                        on_ready=_load,
                    )

            if self.client.chain_session.is_unlocked:
                _require_character()
                return

            self.client.push_state(
                "SolamonWalletState",
                on_ready=_require_character,
            )
            return
        else:
            self.client.push_state("LoadMenuState", load_slot=self.load_slot)
            self.client.pop_state()


class RuleSplash(StartupRule):
    name: ClassVar[str] = "RuleSplash"

    def __init__(
        self,
        client: LocalPygameClient,
        config: TuxemonConfig,
        load_slot: int | None = None,
    ):
        self.client = client
        self.config = config
        self.load_slot = load_slot

    def should_apply(self) -> bool:
        return (
            not self.client.config.chain_enabled
            and self.config.splash
            and self.load_slot is None
        )

    def apply(self) -> None:
        self.client.push_state("SplashState", parent=self.client.state_manager)
        self.client.push_state("FadeInTransition")


class RuleMods(StartupRule):
    name: ClassVar[str] = "RuleMods"

    def __init__(self, client: LocalPygameClient, config: TuxemonConfig):
        self.client = client
        self.config = config

    def should_apply(self) -> bool:
        return (
            not self.client.config.chain_enabled
            and self.config.skip_titlescreen
            and bool(self.config.mods)
        )

    def apply(self) -> None:
        if len(self.config.mods) == 1:
            launcher = GameLauncher(self.client)
            meta = db.mod_metadata.get_mod_metadata(self.config.mods[0])
            launcher.launch(session=local_session, meta=meta)
        else:
            self.client.push_state("ModsChoice", mods=self.config.mods)


class RuleChainEntry(StartupRule):
    name: ClassVar[str] = "RuleChainEntry"

    def __init__(
        self,
        client: LocalPygameClient,
        config: TuxemonConfig,
        load_slot: int | None = None,
    ):
        self.client = client
        self.config = config
        self.load_slot = load_slot

    def should_apply(self) -> bool:
        return self.client.config.chain_enabled and self.load_slot is None

    def apply(self) -> None:
        if self.config.skip_titlescreen:
            self.client.push_state("StartState")


class StartupStateMachine:
    def __init__(
        self,
        client: LocalPygameClient,
        config: TuxemonConfig,
        load_slot: int | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.load_slot = load_slot

        self.rules = [
            RuleBackground(client=client),
            RuleChainEntry(client=client, config=config, load_slot=load_slot),
            RuleIntro(client=client, config=config),
            RuleLoadSlot(client=client, load_slot=load_slot),
            RuleSplash(client=client, config=config, load_slot=load_slot),
            RuleMods(client=client, config=config),
        ]

        self.rules.extend(load_mod_startup_rules(client, config, load_slot))

    def run(self) -> None:
        for rule in self.rules:
            if rule.should_apply():
                from tuxemon.prepare import _startup_trace

                _startup_trace(f"startup_rule: {rule.__class__.__name__}")
                logger.debug(
                    f"Applying startup rule: {rule.__class__.__name__}"
                )
                rule.apply()
