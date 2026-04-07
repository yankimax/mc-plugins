#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict, List

from base import SpeechMorpherModule


class NekoMorpher(SpeechMorpherModule):
    module_id = 'neko'
    priority = 6100
    display_names = {
        'en': 'Neko inserts',
        'ru': 'Неко-вставки',
    }

    _RU_INSERTS: List[str] = ['ня', 'мур', 'мяу']
    _EN_INSERTS: List[str] = ['nya', 'mrr', 'meow']

    def is_active(self, context: Dict[str, Any]) -> bool:
        character_id = str(context.get('character_id') or '').strip().lower()
        if character_id in ('alice', 'neko'):
            return True

        shyness = self.trait(context, 'shyness', 0.0)
        obedience = self.trait(context, 'obedience', 0.0)
        return (shyness + obedience) > 1.2

    def apply(self, text: str, payload: Dict[str, Any], context: Dict[str, Any]) -> str:
        source = str(text or '').strip()
        if not source:
            return source

        rng = context.get('rng')
        if rng is None:
            return source
        if rng.random() > 0.35:
            return source

        locale = self.locale(context)
        inserts = self._RU_INSERTS if locale.startswith('ru') else self._EN_INSERTS
        token = inserts[int(rng.random() * len(inserts)) % len(inserts)]

        if ',' in source and rng.random() < 0.5:
            parts = source.split(',', 1)
            left = parts[0].strip()
            right = parts[1].strip()
            if left and right:
                return f'{left}, {token}, {right}'

        return self.bridge.inject_token(source, token, position='end')


def create_module() -> NekoMorpher:
    return NekoMorpher()
