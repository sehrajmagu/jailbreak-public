"""How B learns a bot's winRubric: the "agreed configuration interface".

docs/INTERFACE.md says A stores BotConfig and B reads it by ``id``, but does not say
how. This small interface is the proposal. Part A implements it on top of Supabase
and must return a JudgeBotConfig (so the system prompt and password never reach
the judge). B ships only the in-memory version, for tests and development.
"""

from abc import ABC, abstractmethod
from typing import Dict, Iterable, Optional, Union

from engine.judge.models import JudgeBotConfig


class BotConfigSource(ABC):
    @abstractmethod
    def get_bot_config(self, bot_id: str) -> Optional[JudgeBotConfig]:
        """Return the bot's config, or None if there is no such bot."""


class InMemoryBotConfigSource(BotConfigSource):
    def __init__(self, bots: Optional[Iterable[Union[JudgeBotConfig, dict]]] = None) -> None:
        self._bots = {}  # type: Dict[str, JudgeBotConfig]
        for bot in bots or []:
            self.add(bot)

    def add(self, bot: Union[JudgeBotConfig, dict]) -> None:
        config = bot if isinstance(bot, JudgeBotConfig) else JudgeBotConfig.model_validate(bot)
        self._bots[config.id] = config

    def get_bot_config(self, bot_id: str) -> Optional[JudgeBotConfig]:
        return self._bots.get(bot_id)
