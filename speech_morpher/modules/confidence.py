#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict

from base import SpeechMorpherModule


class ConfidenceMorpher(SpeechMorpherModule):
    module_id = 'confidence'
    priority = 7000
    display_names = {
        'en': 'Confidence shift',
        'ru': 'Смена уверенности',
    }

    def is_active(self, context: Dict[str, Any]) -> bool:
        confidence = self.trait(context, 'confidence', 0.0)
        shyness = self.trait(context, 'shyness', 0.0)
        return abs(confidence) > 0.12 or shyness > 0.45

    def apply(self, text: str, payload: Dict[str, Any], context: Dict[str, Any]) -> str:
        source = str(text or '').strip()
        if not source:
            return source

        rng = context.get('rng')
        if rng is None:
            return source

        confidence = self.trait(context, 'confidence', 0.0)
        shyness = self.trait(context, 'shyness', 0.0)

        out = source

        if confidence > 0.35 and rng.random() < 0.45:
            out = self._boost_punctuation(out)

        hesitation = max(0.0, min(0.85, shyness * 0.85 + max(0.0, -confidence) * 0.45))
        if hesitation > 0.2 and rng.random() < hesitation:
            out = self._inject_stutter(out, rng)

        return out

    def _boost_punctuation(self, text: str) -> str:
        value = text.rstrip()
        if value.endswith('...'):
            return value
        if value.endswith('?'):
            return f'{value}!' if not value.endswith('?!') else value
        if value.endswith('!'):
            return f'{value}!' if not value.endswith('!!!') else value
        if value.endswith('.'):
            return f'{value[:-1]}!'
        return f'{value}!'

    def _inject_stutter(self, text: str, rng: Any) -> str:
        words = text.split()
        if not words:
            return text

        limit = min(3, len(words))
        index = int(rng.random() * limit)
        index = max(0, min(limit - 1, index))
        original = words[index]
        if len(original) < 2:
            return text

        first = original[0]
        if not first.isalpha():
            return text

        words[index] = f'{first}-{original}'
        return ' '.join(words)


def create_module() -> ConfidenceMorpher:
    return ConfidenceMorpher()
