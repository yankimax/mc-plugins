#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from typing import Any, Dict

from base import SpeechMorpherModule


class CapsMorpher(SpeechMorpherModule):
    module_id = 'caps'
    priority = 8200
    display_names = {
        'en': 'Caps impulse',
        'ru': 'Капс импульс',
    }

    def is_active(self, context: Dict[str, Any]) -> bool:
        energy = self.trait(context, 'energy', 0.0)
        playfulness = self.trait(context, 'playfulness', 0.0)
        return (energy + playfulness) > 0.15

    def apply(self, text: str, payload: Dict[str, Any], context: Dict[str, Any]) -> str:
        source = str(text or '').strip()
        if not source:
            return source

        rng = context.get('rng')
        if rng is None:
            return source

        energy = self.trait(context, 'energy', 0.0)
        confidence = self.trait(context, 'confidence', 0.0)
        impulse = max(0.0, min(1.0, 0.18 + energy * 0.42 + confidence * 0.25))

        # Full caps for high impulse, partial caps for medium impulse.
        if rng.random() < impulse:
            return source.upper()

        words = source.split()
        if len(words) < 2:
            return source

        updated = []
        for word in words:
            if len(updated) >= 10:
                updated.append(word)
                continue
            if rng.random() < impulse * 0.35:
                updated.append(word.upper())
            else:
                updated.append(word)
        return ' '.join(updated)


def create_module() -> CapsMorpher:
    return CapsMorpher()
