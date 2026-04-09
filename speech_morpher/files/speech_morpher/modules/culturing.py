#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from typing import Any, Dict

from base import SpeechMorpherModule


class CulturingMorpher(SpeechMorpherModule):
    module_id = 'culturing'
    priority = 5200
    display_names = {
        'en': 'Casual speech',
        'ru': 'Разговорный стиль',
    }

    _RU_REPLACEMENTS = {
        'привет': ['дарова', 'йоу'],
        'пожалуйста': ['пжлст', 'плиз'],
        'спасибо': ['спс', 'сенкс'],
        'сейчас': ['щас'],
        'давай': ['го'],
        'нормально': ['норм', 'нормас'],
    }

    _EN_REPLACEMENTS = {
        'hello': ['hey', 'yo'],
        'please': ['pls'],
        'thanks': ['thx'],
        'right now': ['now'],
        'let us': ["let's"],
    }

    def is_active(self, context: Dict[str, Any]) -> bool:
        friendliness = self.trait(context, 'friendliness', 0.0)
        playfulness = self.trait(context, 'playfulness', 0.0)
        return (friendliness + playfulness) > 0.55

    def apply(self, text: str, payload: Dict[str, Any], context: Dict[str, Any]) -> str:
        source = str(text or '').strip()
        if not source:
            return source

        rng = context.get('rng')
        if rng is None:
            return source

        locale = self.locale(context)
        if locale.startswith('ru'):
            replacements = self._RU_REPLACEMENTS
        else:
            replacements = self._EN_REPLACEMENTS

        chance = max(0.15, min(0.75, 0.2 + self.trait(context, 'playfulness', 0.0) * 0.5))
        updates = 0
        out = source

        for src, options in replacements.items():
            if updates >= 3:
                break
            if rng.random() > chance:
                continue
            repl = options[int(rng.random() * len(options)) % len(options)]
            updated = self._replace_word(out, src, repl)
            if updated != out:
                out = updated
                updates += 1

        return out

    def _replace_word(self, text: str, needle: str, replacement: str) -> str:
        pattern = re.compile(rf'\b{re.escape(needle)}\b', flags=re.IGNORECASE)

        def _repl(match: re.Match[str]) -> str:
            raw = match.group(0)
            if raw.isupper():
                return replacement.upper()
            if raw and raw[0].isupper():
                return replacement[:1].upper() + replacement[1:]
            return replacement

        return pattern.sub(_repl, text, count=1)


def create_module() -> CulturingMorpher:
    return CulturingMorpher()
